#!/usr/bin/env python
# -*- coding: utf-8 -*-

import argparse
import json
import re
import time
from pathlib import Path
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, Dict, Any

import logging
from utils.logger import setup_logging
from utils.config import load_settings
from core.ai_client import AIClient
from storage.database import Database
from core.document_processor import DocumentProcessor

logger = logging.getLogger(__name__)


def _safe_name(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1F]', "_", name)
    name = name.strip().strip(".")
    return name or "document"


def _iter_input_files(p: Path):
    exts = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"}
    if p.is_file():
        yield p
        return
    if p.is_dir():
        for fp in sorted(p.rglob("*")):
            if fp.is_file() and fp.suffix.lower() in exts:
                yield fp
        return
    raise FileNotFoundError(f"Не найдено: {p}")


def _process_single_file(processor: DocumentProcessor, file_path: Path, out_dir: Path, reuse_result: Optional[Dict[str, Any]] = None):
    """Обработать один файл и вернуть результат."""
    # Если передан готовый результат (для дубликатов), используем его
    if reuse_result is not None:
        per_name = _safe_name(file_path.stem) + ".json"
        per_path = out_dir / per_name
        payload = {"source_file": str(file_path), "error": "", "result": reuse_result}
        with per_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return payload
    
    try:
        result = processor.process_file(file_path)
        error = ""
        # Проверяем, есть ли ошибка rate limit в результате
        if result and isinstance(result, dict):
            # Для PDF с несколькими страницами проверяем pages
            if "pages" in result:
                for page in result.get("pages", []):
                    if page.get("error") and "RATE_LIMIT" in str(page.get("error", "")):
                        error = "RATE_LIMIT"
                        result = None
                        break
            # Для одиночных документов проверяем error напрямую
            elif result.get("error") and "RATE_LIMIT" in str(result.get("error", "")):
                error = "RATE_LIMIT"
                result = None
    except Exception as e:
        result = None
        error_str = str(e)
        if "RATE_LIMIT" in error_str or "rate limit" in error_str.lower():
            error = "RATE_LIMIT"
        else:
            error = f"{type(e).__name__}: {e}"

    per_name = _safe_name(file_path.stem) + ".json"
    per_path = out_dir / per_name

    payload = {"source_file": str(file_path), "error": error, "result": result}

    with per_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    return payload


def main():
    parser = argparse.ArgumentParser(description="Распознавание документов (JPG/PNG/PDF) -> JSON")
    parser.add_argument("file", help="Путь к файлу (jpg/png/pdf) ИЛИ папке с файлами")
    parser.add_argument("--out", default="out/result.json", help="Куда сохранить JSON (для папки: папка out/)")
    parser.add_argument("--log-level", default="INFO", help="Уровень логирования")
    args = parser.parse_args()

    setup_logging(args.log_level)

    settings = load_settings()
    ai = AIClient(
        api_key=settings.openai_api_key, 
        model=settings.openai_model,
        max_concurrency=settings.max_openai_concurrency,
        min_interval_sec=settings.openai_min_interval_sec,
        max_retries=settings.openai_max_retries
    )
    db = Database(settings.db_path)
    processor = DocumentProcessor(ai=ai, db=db)

    in_path = Path(args.file)
    files = list(_iter_input_files(in_path))
    if not files:
        raise ValueError(f"В папке нет поддерживаемых файлов: {in_path}")

    is_batch = in_path.is_dir() or len(files) > 1

    if not is_batch:
        result = processor.process_file(files[0])
        out_path = processor.save_json(result, Path(args.out))
        print(f"✅ Готово: {out_path}")
        return

    out_arg = Path(args.out)
    out_dir = out_arg if out_arg.suffix.lower() != ".json" else out_arg.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    # Начало отсчета времени
    start_time = time.time()

    # Дедупликация: группируем файлы по SHA256
    hash_to_files = defaultdict(list)
    for fp in files:
        file_hash = processor.get_file_hash(fp)
        hash_to_files[file_hash].append(fp)

    # Определяем уникальные файлы (первые в каждой группе) и дубликаты
    unique_files = []
    duplicate_map = {}  # file_path -> (original_file_path, result)
    
    for file_hash, file_list in hash_to_files.items():
        if len(file_list) > 1:
            # Есть дубликаты
            original = file_list[0]
            unique_files.append(original)
            for dup in file_list[1:]:
                duplicate_map[dup] = original
                logger.info(f"Duplicate (same hash) -> reuse result: {dup.name} -> {original.name}")
        else:
            # Уникальный файл
            unique_files.append(file_list[0])

    documents = []
    processed_results = {}  # file_path -> result (для переиспользования дубликатов)
    
    # Параллельная обработка уникальных файлов
    max_workers = settings.max_workers
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Запускаем обработку уникальных файлов
        future_to_file = {
            executor.submit(_process_single_file, processor, fp, out_dir): fp 
            for fp in unique_files
        }
        
        # Собираем результаты по мере завершения
        for future in as_completed(future_to_file):
            file_path = future_to_file[future]
            try:
                payload = future.result()
                processed_results[file_path] = payload["result"]
                documents.append(payload)
            except Exception as e:
                # Если произошла ошибка при обработке
                per_name = _safe_name(file_path.stem) + ".json"
                per_path = out_dir / per_name
                payload = {
                    "source_file": str(file_path), 
                    "error": f"{type(e).__name__}: {e}", 
                    "result": None
                }
                with per_path.open("w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
                documents.append(payload)

    # Обрабатываем дубликаты (используем результаты оригиналов)
    duplicates_reused = 0
    for dup_file, original_file in duplicate_map.items():
        if original_file in processed_results:
            reuse_result = processed_results[original_file]
            payload = _process_single_file(processor, dup_file, out_dir, reuse_result=reuse_result)
            documents.append(payload)
            duplicates_reused += 1

    # Сортируем документы по исходному порядку файлов
    file_to_index = {fp: idx for idx, fp in enumerate(files)}
    documents.sort(key=lambda d: file_to_index.get(Path(d["source_file"]), 999999))

    # Собираем статистику
    processor_stats = processor.get_stats()
    openai_requests = ai.get_request_count()
    elapsed_time = time.time() - start_time

    summary_path = out_dir / "result_all.json"
    summary = {"input": str(in_path), "count": len(files), "documents": documents}
    with summary_path.open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    # Выводим статистику
    print(f"✅ Готово: {summary_path}")
    print(f"✅ Сохранены отдельные JSON для {len(files)} файлов в: {out_dir}")
    print(f"📊 Stats: files={len(files)} | cache_hit={processor_stats['cache_hit']} | cache_miss={processor_stats['cache_miss']} | duplicates_reused={duplicates_reused} | openai_requests={openai_requests} | time={elapsed_time:.1f}s")


if __name__ == "__main__":
    main()
