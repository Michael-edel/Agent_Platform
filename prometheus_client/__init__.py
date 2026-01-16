"""
Встроенный shim-модуль prometheus_client для тестов.

Минимальная реализация Prometheus клиента без внешних зависимостей.
Поддерживает только базовые метрики (Counter, Histogram) и экспорт в текстовом формате.
"""

from typing import Dict, List, Optional, Any
from collections import defaultdict
import threading


class CollectorRegistry:
    """Реестр коллекторов метрик."""
    
    def __init__(self):
        self._collectors: Dict[str, Any] = {}
        self._lock = threading.Lock()
    
    def register(self, collector):
        """Зарегистрировать коллектор."""
        with self._lock:
            name = getattr(collector, '_name', None)
            if name:
                self._collectors[name] = collector
    
    def collect(self):
        """Собрать все метрики из коллекторов."""
        with self._lock:
            for collector in self._collectors.values():
                yield from collector.collect()


# Глобальный реестр
REGISTRY = CollectorRegistry()
REGISTRY._names_to_collectors = {}


class MetricFamily:
    """Семейство метрик (для экспорта)."""
    
    def __init__(self, name: str, metric_type: str, help_text: str = ""):
        self.name = name
        self.type = metric_type
        self.help = help_text
        self.samples: List[Dict[str, Any]] = []


class Counter:
    """Счётчик метрик."""
    
    def __init__(self, name: str, documentation: str = "", labelnames: Optional[List[str]] = None):
        self._name = name
        self._documentation = documentation
        self._labelnames = labelnames or []
        self._values: Dict[tuple, float] = defaultdict(float)
        self._lock = threading.Lock()
        
        # Регистрируем в глобальном реестре
        if not hasattr(REGISTRY, '_names_to_collectors'):
            REGISTRY._names_to_collectors = {}
        REGISTRY._names_to_collectors[name] = self
        REGISTRY.register(self)
    
    def labels(self, **kwargs):
        """Вернуть объект для работы с конкретными метками."""
        return _LabeledCounter(self, tuple(sorted(kwargs.items())))
    
    def inc(self, value: float = 1.0):
        """Увеличить счётчик."""
        with self._lock:
            self._values[()] += value
    
    def collect(self):
        """Собрать метрики для экспорта."""
        family = MetricFamily(self._name, "counter", self._documentation)
        with self._lock:
            for labels_tuple, value in self._values.items():
                labels_dict = dict(labels_tuple) if labels_tuple else {}
                labels_str = ",".join(f'{k}="{v}"' for k, v in sorted(labels_dict.items()))
                metric_name = f'{self._name}{{{labels_str}}}' if labels_str else self._name
                family.samples.append({
                    "name": metric_name,
                    "value": value,
                    "labels": labels_dict
                })
        yield family


class _LabeledCounter:
    """Обёртка для счётчика с метками."""
    
    def __init__(self, counter: Counter, labels_tuple: tuple):
        self._counter = counter
        self._labels_tuple = labels_tuple
    
    def inc(self, value: float = 1.0):
        """Увеличить счётчик с метками."""
        with self._counter._lock:
            self._counter._values[self._labels_tuple] += value


class Histogram:
    """Гистограмма метрик."""
    
    def __init__(self, name: str, documentation: str = "", labelnames: Optional[List[str]] = None, buckets: Optional[List[float]] = None):
        self._name = name
        self._documentation = documentation
        self._labelnames = labelnames or []
        self._buckets = buckets or [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, float('inf')]
        self._values: Dict[tuple, List[float]] = defaultdict(list)
        self._sums: Dict[tuple, float] = defaultdict(float)
        self._counts: Dict[tuple, int] = defaultdict(int)
        self._lock = threading.Lock()
        
        # Регистрируем в глобальном реестре
        if not hasattr(REGISTRY, '_names_to_collectors'):
            REGISTRY._names_to_collectors = {}
        REGISTRY._names_to_collectors[name] = self
        REGISTRY.register(self)
    
    def labels(self, **kwargs):
        """Вернуть объект для работы с конкретными метками."""
        return _LabeledHistogram(self, tuple(sorted(kwargs.items())))
    
    def observe(self, value: float):
        """Добавить наблюдение."""
        with self._lock:
            self._values[()].append(value)
            self._sums[()] += value
            self._counts[()] += 1
    
    def collect(self):
        """Собрать метрики для экспорта."""
        family = MetricFamily(self._name, "histogram", self._documentation)
        with self._lock:
            for labels_tuple in set(list(self._values.keys()) + list(self._sums.keys())):
                labels_dict = dict(labels_tuple) if labels_tuple else {}
                labels_str = ",".join(f'{k}="{v}"' for k, v in sorted(labels_dict.items()))
                
                values = self._values.get(labels_tuple, [])
                total_sum = self._sums.get(labels_tuple, 0.0)
                total_count = self._counts.get(labels_tuple, 0)
                
                # Экспортируем _sum и _count
                if labels_str:
                    sum_name = f'{self._name}_sum{{{labels_str}}}'
                    count_name = f'{self._name}_count{{{labels_str}}}'
                else:
                    sum_name = f'{self._name}_sum'
                    count_name = f'{self._name}_count'
                
                family.samples.append({
                    "name": sum_name,
                    "value": total_sum,
                    "labels": labels_dict
                })
                family.samples.append({
                    "name": count_name,
                    "value": total_count,
                    "labels": labels_dict
                })
                
                # Экспортируем buckets
                bucket_counts = [0] * len(self._buckets)
                for v in values:
                    for i, bucket in enumerate(self._buckets):
                        if v <= bucket:
                            bucket_counts[i] += 1
                            break
                
                for i, bucket in enumerate(self._buckets):
                    if bucket == float('inf'):
                        bucket_str = "+Inf"
                    else:
                        bucket_str = str(bucket)
                    
                    if labels_str:
                        bucket_name = f'{self._name}_bucket{{{labels_str},le="{bucket_str}"}}'
                    else:
                        bucket_name = f'{self._name}_bucket{{le="{bucket_str}"}}'
                    
                    family.samples.append({
                        "name": bucket_name,
                        "value": bucket_counts[i],
                        "labels": {**labels_dict, "le": bucket_str}
                    })
        yield family


class _LabeledHistogram:
    """Обёртка для гистограммы с метками."""
    
    def __init__(self, histogram: Histogram, labels_tuple: tuple):
        self._histogram = histogram
        self._labels_tuple = labels_tuple
    
    def observe(self, value: float):
        """Добавить наблюдение с метками."""
        with self._histogram._lock:
            self._histogram._values[self._labels_tuple].append(value)
            self._histogram._sums[self._labels_tuple] += value
            self._histogram._counts[self._labels_tuple] += 1


class Gauge:
    """Gauge метрика (упрощённая реализация)."""
    
    def __init__(self, name: str, documentation: str = "", labelnames: Optional[List[str]] = None):
        self._name = name
        self._documentation = documentation
        self._labelnames = labelnames or []
        self._values: Dict[tuple, float] = defaultdict(float)
        self._lock = threading.Lock()
        
        # Регистрируем в глобальном реестре
        if not hasattr(REGISTRY, '_names_to_collectors'):
            REGISTRY._names_to_collectors = {}
        REGISTRY._names_to_collectors[name] = self
        REGISTRY.register(self)
    
    def labels(self, **kwargs):
        """Вернуть объект для работы с конкретными метками."""
        return _LabeledGauge(self, tuple(sorted(kwargs.items())))
    
    def set(self, value: float):
        """Установить значение."""
        with self._lock:
            self._values[()] = value
    
    def inc(self, value: float = 1.0):
        """Увеличить значение."""
        with self._lock:
            self._values[()] += value
    
    def dec(self, value: float = 1.0):
        """Уменьшить значение."""
        with self._lock:
            self._values[()] -= value
    
    def collect(self):
        """Собрать метрики для экспорта."""
        family = MetricFamily(self._name, "gauge", self._documentation)
        with self._lock:
            for labels_tuple, value in self._values.items():
                labels_dict = dict(labels_tuple) if labels_tuple else {}
                labels_str = ",".join(f'{k}="{v}"' for k, v in sorted(labels_dict.items()))
                metric_name = f'{self._name}{{{labels_str}}}' if labels_str else self._name
                family.samples.append({
                    "name": metric_name,
                    "value": value,
                    "labels": labels_dict
                })
        yield family


class _LabeledGauge:
    """Обёртка для gauge с метками."""
    
    def __init__(self, gauge: Gauge, labels_tuple: tuple):
        self._gauge = gauge
        self._labels_tuple = labels_tuple
    
    def set(self, value: float):
        """Установить значение с метками."""
        with self._gauge._lock:
            self._gauge._values[self._labels_tuple] = value
    
    def inc(self, value: float = 1.0):
        """Увеличить значение с метками."""
        with self._gauge._lock:
            self._gauge._values[self._labels_tuple] += value
    
    def dec(self, value: float = 1.0):
        """Уменьшить значение с метками."""
        with self._gauge._lock:
            self._gauge._values[self._labels_tuple] -= value


def generate_latest(registry: CollectorRegistry) -> bytes:
    """Сгенерировать текстовый формат Prometheus из реестра."""
    lines = []
    
    # Собираем все семейства метрик
    families: Dict[str, MetricFamily] = {}
    
    for collector in registry._collectors.values():
        for family in collector.collect():
            if family.name not in families:
                families[family.name] = family
            else:
                # Объединяем samples
                families[family.name].samples.extend(family.samples)
    
    # Генерируем вывод
    for family_name in sorted(families.keys()):
        family = families[family_name]
        
        # HELP строка
        if family.help:
            lines.append(f"# HELP {family_name} {family.help}")
        
        # TYPE строка
        lines.append(f"# TYPE {family_name} {family.type}")
        
        # Samples
        for sample in family.samples:
            name = sample["name"]
            value = sample["value"]
            lines.append(f"{name} {value}")
    
    return "\n".join(lines).encode("utf-8")


# Константа для content type
CONTENT_TYPE_LATEST = "text/plain; version=0.0.4; charset=utf-8"
