import logging
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Any

import httpx
from PySide6.QtCore import QThread, Signal


logger = logging.getLogger(__name__)

DEFAULT_HTTP_READ_TIMEOUT_SECONDS = 180


def build_http_timeout(read_timeout_seconds: int | float = DEFAULT_HTTP_READ_TIMEOUT_SECONDS) -> httpx.Timeout:
    return httpx.Timeout(
        connect=20.0,
        read=float(read_timeout_seconds),
        write=60.0,
        pool=20.0,
    )


class FetchCancelled(Exception):
    pass


class HttpWorker(QThread):
    ok = Signal(object, float)
    fail = Signal(str)

    def __init__(self, url: str, params: dict[str, Any] | None = None, read_timeout_seconds: int = DEFAULT_HTTP_READ_TIMEOUT_SECONDS):
        super().__init__()
        self.url = url
        self.params = params or {}
        self.read_timeout_seconds = read_timeout_seconds

    def run(self):
        started = time.perf_counter()
        try:
            params = dict(self.params)
            params.setdefault("f", "json")

            with httpx.Client(timeout=build_http_timeout(self.read_timeout_seconds), follow_redirects=True) as client:
                if self.isInterruptionRequested():
                    return
                response = client.post(self.url, params=params)
                if self.isInterruptionRequested():
                    return
                response.raise_for_status()
                data = response.json()
                if self.isInterruptionRequested():
                    return

                self.raise_for_arcgis_error(data)
                elapsed_ms = (time.perf_counter() - started) * 1000
                self.ok.emit(data, elapsed_ms)

        except httpx.TimeoutException as exc:
            logger.exception("HTTP request timed out")
            self.fail.emit(f"Request timed out after {self.read_timeout_seconds} seconds while waiting for the server response: {exc}")
        except Exception as exc:
            logger.exception("HTTP request failed")
            self.fail.emit(str(exc))

    @staticmethod
    def raise_for_arcgis_error(data: object) -> None:
        if isinstance(data, dict) and "error" in data:
            err = data["error"]
            message = err.get("message", "ArcGIS Server REST error")
            details = err.get("details", [])
            code = err.get("code", "")
            detail_text = "\n".join(str(d) for d in details)
            raise RuntimeError(f"ArcGIS error {code}: {message}\n{detail_text}".strip())


class FetchAllWorker(QThread):
    ok = Signal(object, float, int)
    fail = Signal(str)
    cancelled = Signal()
    progress = Signal(int, int)

    def __init__(self, url: str, params: dict[str, Any], page_size: int, max_workers: int = 4, read_timeout_seconds: int = DEFAULT_HTTP_READ_TIMEOUT_SECONDS):
        super().__init__()
        self.url = url
        self.params = dict(params)
        self.page_size = max(1, int(page_size))
        self.max_workers = max(1, int(max_workers))
        self.read_timeout_seconds = read_timeout_seconds

    def run(self):
        started = time.perf_counter()
        try:
            with httpx.Client(timeout=build_http_timeout(self.read_timeout_seconds), follow_redirects=True) as client:
                self.raise_if_cancelled()
                total = self.fetch_count(client)
                self.raise_if_cancelled()
                if total == 0:
                    elapsed_ms = (time.perf_counter() - started) * 1000
                    self.ok.emit({"features": [], "count": 0, "pagesFetched": 0, "parallelFetch": True}, elapsed_ms, 0)
                    return

                offsets = list(range(0, total, self.page_size))
                pages: dict[int, dict[str, Any]] = {}
                completed = 0
                pool = ThreadPoolExecutor(max_workers=min(self.max_workers, len(offsets)))
                futures = {pool.submit(self.fetch_page, offset): offset for offset in offsets}
                pending = set(futures)
                try:
                    while pending:
                        self.raise_if_cancelled()
                        done, pending = wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)
                        for future in done:
                            self.raise_if_cancelled()
                            offset = futures[future]
                            pages[offset] = future.result()
                            completed += 1
                            self.progress.emit(completed, len(offsets))
                except FetchCancelled:
                    for future in pending:
                        future.cancel()
                    pool.shutdown(wait=False, cancel_futures=True)
                    raise
                except Exception:
                    pool.shutdown(wait=False, cancel_futures=True)
                    raise
                else:
                    pool.shutdown(wait=True)

                features: list[dict[str, Any]] = []
                template: dict[str, Any] = {}
                for offset in sorted(pages):
                    page = pages[offset]
                    if not template:
                        template = dict(page)
                    features.extend(page.get("features", []))

                combined = template or {}
                combined["features"] = features
                combined["count"] = total
                combined["pagesFetched"] = len(offsets)
                combined["parallelFetch"] = True
                combined["exceededTransferLimit"] = len(features) < total

                elapsed_ms = (time.perf_counter() - started) * 1000
                self.ok.emit(combined, elapsed_ms, len(offsets))
        except FetchCancelled:
            logger.info("Parallel fetch cancelled")
            self.cancelled.emit()
        except httpx.TimeoutException as exc:
            logger.exception("Parallel fetch all timed out")
            self.fail.emit(f"Parallel fetch timed out after {self.read_timeout_seconds} seconds while waiting for a server response: {exc}")
        except Exception as exc:
            logger.exception("Parallel fetch all failed")
            self.fail.emit(str(exc))

    def raise_if_cancelled(self) -> None:
        if self.isInterruptionRequested():
            raise FetchCancelled()

    def fetch_count(self, client: httpx.Client) -> int:
        params = dict(self.params)
        params.setdefault("f", "json")
        params["returnCountOnly"] = "true"
        params["returnGeometry"] = "false"
        params.pop("resultOffset", None)
        params.pop("resultRecordCount", None)
        params.pop("outFields", None)
        params.pop("orderByFields", None)
        params.pop("outSR", None)

        response = client.get(self.url, params=params)
        response.raise_for_status()
        data = response.json()
        HttpWorker.raise_for_arcgis_error(data)
        count = data.get("count")
        if not isinstance(count, int):
            raise RuntimeError("ArcGIS count request did not return an integer 'count'.")
        return count

    def fetch_page(self, offset: int) -> dict[str, Any]:
        self.raise_if_cancelled()
        params = dict(self.params)
        params.setdefault("f", "json")
        params["resultOffset"] = str(offset)
        params["resultRecordCount"] = str(self.page_size)

        with httpx.Client(timeout=build_http_timeout(self.read_timeout_seconds), follow_redirects=True) as client:
            response = client.post(self.url, params=params)
            response.raise_for_status()
            data = response.json()
        self.raise_if_cancelled()
        HttpWorker.raise_for_arcgis_error(data)
        if not isinstance(data, dict):
            raise RuntimeError("ArcGIS page request did not return a JSON object.")
        return data


class GpJobWorker(QThread):
    status = Signal(str, object)
    ok = Signal(object, float)
    fail = Signal(str)
    cancelled = Signal()

    FINAL_STATUSES = {
        "esriJobSucceeded",
        "esriJobFailed",
        "esriJobCancelled",
        "esriJobCancelling",
        "esriJobTimedOut",
        "esriJobDeleted",
    }

    def __init__(
        self,
        task_url: str,
        params: dict[str, Any],
        poll_interval_seconds: float = 2.0,
        read_timeout_seconds: int = DEFAULT_HTTP_READ_TIMEOUT_SECONDS,
    ):
        super().__init__()
        self.task_url = task_url.rstrip("/")
        self.params = dict(params)
        self.poll_interval_seconds = max(0.5, float(poll_interval_seconds))
        self.read_timeout_seconds = read_timeout_seconds

    def run(self):
        started = time.perf_counter()
        try:
            params = dict(self.params)
            params.setdefault("f", "json")
            with httpx.Client(timeout=build_http_timeout(self.read_timeout_seconds), follow_redirects=True) as client:
                self.raise_if_cancelled()
                submit_response = client.post(f"{self.task_url}/submitJob", data=params)
                submit_response.raise_for_status()
                submit_data = submit_response.json()
                HttpWorker.raise_for_arcgis_error(submit_data)
                job_id = submit_data.get("jobId")
                if not job_id:
                    raise RuntimeError("GP submitJob did not return a jobId.")

                self.status.emit(str(submit_data.get("jobStatus", "submitted")), submit_data)
                status_url = f"{self.task_url}/jobs/{job_id}"
                while True:
                    self.raise_if_cancelled()
                    time.sleep(self.poll_interval_seconds)
                    self.raise_if_cancelled()
                    status_response = client.post(status_url, data={"f": "json", **self.token_params(params)})
                    status_response.raise_for_status()
                    status_data = status_response.json()
                    HttpWorker.raise_for_arcgis_error(status_data)
                    job_status = str(status_data.get("jobStatus", "unknown"))
                    self.status.emit(job_status, status_data)
                    if job_status in self.FINAL_STATUSES:
                        elapsed_ms = (time.perf_counter() - started) * 1000
                        self.ok.emit(status_data, elapsed_ms)
                        return
        except FetchCancelled:
            logger.info("GP job polling cancelled")
            self.cancelled.emit()
        except Exception as exc:
            logger.exception("GP job failed")
            self.fail.emit(str(exc))

    def raise_if_cancelled(self) -> None:
        if self.isInterruptionRequested():
            raise FetchCancelled()

    @staticmethod
    def token_params(params: dict[str, Any]) -> dict[str, Any]:
        token = params.get("token")
        return {"token": token} if token else {}
