"""Обработка файлов документов (jpg/png/pdf) -> JSON."""

import json
import logging
import hashlib
from pathlib import Path
from typing import Dict, Any, List, Optional, Union

from core.ai_client import AIClient
from core.math_corrector import MathCorrector
from utils.validators import DocumentValidator
from utils.extractors import merge_invoice_fields
from integrations.pdf_processor import pdf_to_pages
from integrations.barcode_reader import decode_barcodes_from_pil
from models.document import DocumentData, DocumentItem
from storage.database import Database
from core.inventory_manager import InventoryManager

logger = logging.getLogger(__name__)

PathLike = Union[str, Path]

class DocumentProcessor:
    def __init__(self, ai: AIClient, db: Database, cache_dir: Optional[PathLike] = None):
        self.ai = ai
        self.db = db
        self.corrector = MathCorrector()
        self.validator = DocumentValidator()
        self.inv = InventoryManager(db)
        
        # Настройка кэша
        if cache_dir is None:
            cache_dir = Path("out/cache")
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        # Настройка кэша страниц
        self.cache_pages_dir = Path("out/cache_pages")
        self.cache_pages_dir.mkdir(parents=True, exist_ok=True)
        
        # Статистика
        self.stats = {
            "cache_hit": 0,
            "cache_miss": 0,
        }

    def _compute_file_hash(self, file_path: Path) -> str:
        """Вычислить SHA256 хеш файла."""
        sha256 = hashlib.sha256()
        with file_path.open("rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)
        return sha256.hexdigest()

    def get_file_hash(self, file_path: PathLike) -> str:
        """Получить SHA256 хеш файла (публичный метод для дедупликации)."""
        return self._compute_file_hash(Path(file_path))

    def _get_cache_path(self, file_hash: str) -> Path:
        """Получить путь к файлу кэша."""
        return self.cache_dir / f"{file_hash}.json"

    def _get_page_cache_path(self, file_hash: str, page_num: int) -> Path:
        """Получить путь к файлу кэша страницы."""
        return self.cache_pages_dir / f"{file_hash}_p{page_num}.json"

    def _load_from_cache(self, cache_path: Path) -> Optional[Dict[str, Any]]:
        """Загрузить результат из кэша."""
        try:
            if cache_path.exists():
                with cache_path.open("r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            logger.warning(f"Ошибка при загрузке кэша {cache_path}: {e}")
        return None

    def _save_to_cache(self, cache_path: Path, result: Dict[str, Any]) -> None:
        """Сохранить результат в кэш."""
        try:
            with cache_path.open("w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"Ошибка при сохранении кэша {cache_path}: {e}")

    def _load_page_from_cache(self, cache_path: Path) -> Optional[Dict[str, Any]]:
        """Загрузить страницу из кэша."""
        try:
            if cache_path.exists():
                with cache_path.open("r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            logger.warning(f"Ошибка при загрузке кэша страницы {cache_path}: {e}")
        return None

    def _save_page_to_cache(self, cache_path: Path, page_dict: Dict[str, Any]) -> None:
        """Сохранить страницу в кэш."""
        try:
            with cache_path.open("w", encoding="utf-8") as f:
                json.dump(page_dict, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"Ошибка при сохранении кэша страницы {cache_path}: {e}")

    def process_file(self, file_path: PathLike) -> Dict[str, Any]:
        p = Path(file_path)
        if not p.exists():
            raise FileNotFoundError(str(p))
        
        if p.is_dir():
            raise ValueError(
                f"'{p}' является директорией, а не файлом.\n"
                f"Укажите конкретный файл, например: {p / 'Счет на оплату № ЦБ-11777 от 15.12.2025.pdf'}"
            )
        
        if not p.is_file():
            raise ValueError(f"Путь '{p}' не является файлом")

        # Вычисляем хеш файла для кэширования
        file_hash = self._compute_file_hash(p)
        cache_path = self._get_cache_path(file_hash)

        # Пытаемся загрузить из кэша
        cached_result = self._load_from_cache(cache_path)
        if cached_result is not None:
            logger.info(f"Cache hit: {p.name} (hash: {file_hash[:8]}...)")
            self.stats["cache_hit"] += 1
            return cached_result

        logger.info(f"Cache miss: {p.name} (hash: {file_hash[:8]}...)")
        self.stats["cache_miss"] += 1

        # Обрабатываем файл как обычно
        if p.suffix.lower() == ".pdf":
            pages = pdf_to_pages(p)
            docs: List[Dict[str, Any]] = []
            for idx, (img_bytes, _pil, page_text) in enumerate(pages, 1):
                # Проверяем кэш страницы
                page_cache_path = self._get_page_cache_path(file_hash, idx)
                cached_page = self._load_page_from_cache(page_cache_path)
                
                if cached_page is not None:
                    # Используем закэшированную страницу
                    logger.debug(f"Page cache hit: {p.name} page {idx} (hash: {file_hash[:8]}...)")
                    docs.append(cached_page)
                else:
                    # Обрабатываем страницу через OpenAI
                    logger.debug(f"Page cache miss: {p.name} page {idx} (hash: {file_hash[:8]}...)")
                    doc = self.ai.analyze_document_sync(img_bytes, extra_text=page_text)
                    
                    # Проверяем на ошибку rate limit
                    if doc.error and "RATE_LIMIT" in doc.error:
                        # Если rate limit, возвращаем ошибку в структуре страницы
                        page_dict = {
                            "page_number": idx,
                            "total_pages_processed": len(pages),
                            "error": doc.error,
                            "document_type": "error"
                        }
                        docs.append(page_dict)
                        # Не сохраняем в кэш при ошибке
                        continue
                    
                    doc.page_number = idx
                    doc.total_pages_processed = len(pages)
                    doc = self.corrector.correct_document(doc)
                    page_dict = self._postprocess(doc).to_dict()
                    try:
                        img_barcodes = decode_barcodes_from_pil(_pil)
                        if img_barcodes:
                            page_dict['barcodes'] = list(dict.fromkeys((page_dict.get('barcodes') or []) + img_barcodes))
                    except Exception:
                        pass
                    try:
                        page_dict = merge_invoice_fields(page_dict, page_text)
                    except Exception:
                        pass
                    # Сохраняем страницу в кэш
                    self._save_page_to_cache(page_cache_path, page_dict)
                    docs.append(page_dict)
            result = {"pages": docs, "total_pages": len(docs)}
        else:
            page_text = ""
            img_bytes = p.read_bytes()
            doc = self.ai.analyze_document_sync(img_bytes, extra_text=page_text)
            
            # Проверяем на ошибку rate limit
            if doc.error and "RATE_LIMIT" in doc.error:
                # Возвращаем ошибку в структуре результата
                result = {
                    "error": doc.error,
                    "document_type": "error"
                }
                # Не сохраняем в кэш при ошибке
                return result
            
            doc = self.corrector.correct_document(doc)
            result = self._postprocess(doc).to_dict()

        # Сохраняем результат в кэш
        self._save_to_cache(cache_path, result)
        return result

    def process_directory(self, dir_path: PathLike) -> Dict[str, Any]:
        """Обработать все поддерживаемые файлы в директории"""
        p = Path(dir_path)
        if not p.is_dir():
            raise ValueError(f"'{p}' не является директорией")
        
        # Поддерживаемые расширения
        extensions = {'.pdf', '.jpg', '.jpeg', '.png', '.bmp'}
        files = [f for f in p.iterdir() if f.is_file() and f.suffix.lower() in extensions]
        
        if not files:
            logger.warning(f"В директории '{p}' не найдено поддерживаемых файлов")
            return {"pages": [], "total_pages": 0}
        
        logger.info(f"Найдено {len(files)} файлов в директории '{p}'")
        
        all_pages = []
        for file_path in sorted(files):
            try:
                logger.info(f"Обработка: {file_path.name}")
                result = self.process_file(file_path)
                
                # Если результат - словарь с pages, добавляем их
                if isinstance(result, dict) and "pages" in result:
                    for page in result["pages"]:
                        page["source_file"] = file_path.name
                    all_pages.extend(result["pages"])
                # Если результат - одна страница (dict без pages)
                elif isinstance(result, dict):
                    result["source_file"] = file_path.name
                    all_pages.append(result)
            except Exception as e:
                logger.error(f"Ошибка при обработке {file_path.name}: {e}")
                all_pages.append({
                    "source_file": file_path.name,
                    "error": str(e),
                    "document_type": "error"
                })
        
        return {"pages": all_pages, "total_pages": len(all_pages)}

    def _postprocess(self, doc: DocumentData) -> DocumentData:
        # Валидация арифметики
        items_dicts = [it.to_dict() for it in doc.items]
        ar = self.validator.validate_arithmetic(items_dicts)
        if not ar.is_valid:
            doc.error = (doc.error or "") + ("; " if doc.error else "") + " | ".join(ar.errors)

        # Сопоставление товаров с 1С номенклатурой (если база заполнена)
        mapped_items: List[DocumentItem] = []
        for it in doc.items:
            mapping = self.inv.get_or_suggest_mapping(it.name, it.sku)
            if mapping:
                # сохраняем предложенное сопоставление (с низкой уверенностью — можно перезаписать позже)
                try:
                    self.db.save_mapping(mapping)
                except Exception:
                    pass
                it.sku = it.sku or mapping.supplier_sku
            mapped_items.append(it)

        doc.items = mapped_items
        return doc

    def save_json(self, data: Dict[str, Any], out_path: PathLike) -> Path:
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return out

    def get_stats(self) -> Dict[str, int]:
        """Получить статистику обработки."""
        return self.stats.copy()

    def reset_stats(self) -> None:
        """Сбросить статистику."""
        self.stats = {"cache_hit": 0, "cache_miss": 0}
