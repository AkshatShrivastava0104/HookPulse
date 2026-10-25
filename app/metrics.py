from collections import defaultdict
from typing import Dict, List
import threading

class MetricsCollector:
    def __init__(self):
        self.http_requests = defaultdict(int)
        self.webhook_requests = defaultdict(int)
        self.worker_jobs = defaultdict(int)
        self.latency_buckets = defaultdict(int)
        self.latency_count = 0
        self.latency_sum = 0.0
        self.lock = threading.Lock()
        self.bucket_thresholds = [5.0, 10.0, 25.0, 50.0, 100.0, 250.0, 500.0, 1000.0, 2500.0, 5000.0]

    def inc_http_request(self, path: str, status: int, method: str = "GET"):
        with self.lock:
            # Maintain backward compatible label format while supporting standard Prometheus
            key = f'path="{path}",status="{status}"'
            self.http_requests[key] += 1

    def inc_webhook_request(self, result: str):
        with self.lock:
            key = f'result="{result}"'
            self.webhook_requests[key] += 1

    def inc_worker_job(self, status: str):
        with self.lock:
            key = f'status="{status}"'
            self.worker_jobs[key] += 1

    def observe_latency(self, latency_ms: float):
        with self.lock:
            self.latency_count += 1
            self.latency_sum += latency_ms
            for threshold in self.bucket_thresholds:
                if latency_ms <= threshold:
                    self.latency_buckets[str(int(threshold) if threshold.is_integer() else threshold)] += 1
            self.latency_buckets['+Inf'] += 1

    def generate_metrics(self) -> str:
        with self.lock:
            lines = []

            lines.append('# HELP http_requests_total Total HTTP requests')
            lines.append('# TYPE http_requests_total counter')
            for labels, count in sorted(self.http_requests.items()):
                lines.append(f'http_requests_total{{{labels}}} {count}')

            lines.append('# HELP webhook_requests_total Total webhook requests')
            lines.append('# TYPE webhook_requests_total counter')
            for labels, count in sorted(self.webhook_requests.items()):
                lines.append(f'webhook_requests_total{{{labels}}} {count}')

            if self.worker_jobs:
                lines.append('# HELP worker_jobs_total Total background worker jobs processed')
                lines.append('# TYPE worker_jobs_total counter')
                for labels, count in sorted(self.worker_jobs.items()):
                    lines.append(f'worker_jobs_total{{{labels}}} {count}')

            lines.append('# HELP request_latency_ms_bucket Request latency in milliseconds')
            lines.append('# TYPE request_latency_ms_bucket histogram')
            
            # Sort buckets numerically, with +Inf at the end
            def sort_key(b):
                return float('inf') if b == '+Inf' else float(b)

            for bucket in sorted(self.latency_buckets.keys(), key=sort_key):
                lines.append(f'request_latency_ms_bucket{{le="{bucket}"}} {self.latency_buckets[bucket]}')
            lines.append(f'request_latency_ms_sum {round(self.latency_sum, 2)}')
            lines.append(f'request_latency_ms_count {self.latency_count}')

            return '\n'.join(lines) + '\n'

metrics = MetricsCollector()
