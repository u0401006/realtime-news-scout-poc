"""Firestore 資料載入器 — 串接 AppDev 提供的 Firestore 數據

透過 Firestore REST API 讀取即時數據，使用 ADC / gcloud impersonation 驗證，
無需下載 service account key JSON。

支援兩種模式：
1. Firestore REST API 模式：透過 gcloud ADC 取得 access token
2. JSON 快取模式：讀取本地 JSON 快取檔（離線/測試用）

使用方式：
    from ranking.firebase_loader import FirebaseLoader

    loader = FirebaseLoader(
        project_id="medialab-356306",
        service_account="firebase-adminsdk-enwek@medialab-356306.iam.gserviceaccount.com",
    )
    docs = loader.get_collection("localnews/Recent50LocalNews1/news")

    # 或使用本地快取
    loader = FirebaseLoader(cache_path="data/firebase_cache.json")
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT: int = 10
_FIRESTORE_BASE = "https://firestore.googleapis.com/v1"


@dataclass
class FirebaseEntry:
    """Firestore 文件條目。"""
    path: str
    data: Any
    fetched_at: str
    source: str  # "remote" | "cache"


@dataclass
class FirebaseTrendingItem:
    """Firebase trending headline 條目（AppDev 提供的格式）。"""
    headline_id: str
    title: str
    boost: float
    category: str = ""
    region: str = ""
    updated_at: str = ""


def _get_access_token(service_account: Optional[str] = None) -> str:
    """透過 gcloud CLI 取得 access token（支援 impersonation）。"""
    cmd = ["gcloud", "auth", "print-access-token"]
    if service_account:
        cmd += [f"--impersonate-service-account={service_account}"]
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=15,
    )
    if result.returncode != 0:
        raise RuntimeError(f"gcloud auth failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _parse_firestore_value(val: Dict[str, Any]) -> Any:
    """將 Firestore REST API 的 typed value 轉為 Python 原生型別。"""
    if "stringValue" in val:
        return val["stringValue"]
    if "integerValue" in val:
        return int(val["integerValue"])
    if "doubleValue" in val:
        return float(val["doubleValue"])
    if "booleanValue" in val:
        return val["booleanValue"]
    if "nullValue" in val:
        return None
    if "timestampValue" in val:
        return val["timestampValue"]
    if "arrayValue" in val:
        return [_parse_firestore_value(v) for v in val["arrayValue"].get("values", [])]
    if "mapValue" in val:
        return {
            k: _parse_firestore_value(v)
            for k, v in val["mapValue"].get("fields", {}).items()
        }
    return val


def _parse_firestore_doc(doc: Dict[str, Any]) -> Dict[str, Any]:
    """將 Firestore document 轉為扁平 dict。"""
    fields = doc.get("fields", {})
    return {k: _parse_firestore_value(v) for k, v in fields.items()}


class FirebaseLoader:
    """Firestore 資料載入器。

    串接 AppDev 端推送至 Firestore 的即時數據，
    提供 trending headlines、user engagement metrics 等資料。
    使用 gcloud ADC / impersonation 驗證，不需要 key JSON。
    """

    def __init__(
        self,
        project_id: Optional[str] = None,
        cache_path: Optional[str] = None,
        service_account: Optional[str] = None,
        database: str = "(default)",
        timeout: int = _DEFAULT_TIMEOUT,
    ) -> None:
        """初始化 FirebaseLoader。

        Args:
            project_id: GCP 專案 ID (e.g. "medialab-356306")。
            cache_path: 本地 JSON 快取檔路徑。
            service_account: 用於 impersonation 的 SA email（可選，若已設定 ADC 可省略）。
            database: Firestore database name。
            timeout: HTTP 請求超時秒數。
        """
        self._project_id = project_id
        self._service_account = service_account
        self._database = database
        self._timeout = timeout
        self._cache: Dict[str, Any] = {}
        self._trending_items: List[FirebaseTrendingItem] = []
        self._access_token: Optional[str] = None

        if cache_path:
            self.load_cache(cache_path)

        logger.info(
            "FirebaseLoader initialized: remote=%s, cache_entries=%d",
            "enabled" if self._project_id else "disabled",
            len(self._cache),
        )

    @property
    def trending_count(self) -> int:
        """已載入的 trending items 數量。"""
        return len(self._trending_items)

    @property
    def trending_items(self) -> List[FirebaseTrendingItem]:
        """已載入的 trending items。"""
        return list(self._trending_items)

    def load_cache(self, cache_path: str) -> None:
        """載入本地 JSON 快取。

        預期格式：
        {
            "trending/headlines": {
                "item1": {"title": "...", "boost": 10.0, ...},
                ...
            },
            "config/scorer": {
                "threshold_override": null,
                ...
            }
        }

        Args:
            cache_path: JSON 快取檔路徑。

        Raises:
            FileNotFoundError: 檔案不存在。
        """
        path = Path(cache_path)
        if not path.exists():
            raise FileNotFoundError(f"Firebase cache not found: {cache_path}")

        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"Firebase cache must be a JSON object: {cache_path}")

        self._cache = data
        logger.info("Loaded Firebase cache: %d paths from %s", len(data), cache_path)

        # 解析 trending headlines
        self._parse_trending()

    def _parse_trending(self) -> None:
        """解析 cache 中的 trending headlines 數據。"""
        trending_data = self._cache.get("trending/headlines") or self._cache.get("trending")
        if not trending_data or not isinstance(trending_data, dict):
            return

        items: List[FirebaseTrendingItem] = []
        for key, val in trending_data.items():
            if not isinstance(val, dict):
                continue
            items.append(FirebaseTrendingItem(
                headline_id=key,
                title=val.get("title", ""),
                boost=float(val.get("boost", 0.0)),
                category=val.get("category", ""),
                region=val.get("region", ""),
                updated_at=val.get("updated_at", ""),
            ))

        self._trending_items = sorted(items, key=lambda x: -x.boost)
        logger.info("Parsed %d trending items from Firebase", len(items))

    def _ensure_token(self) -> str:
        """取得或刷新 access token。"""
        if not self._access_token:
            self._access_token = _get_access_token(self._service_account)
        return self._access_token

    def _invalidate_token(self) -> None:
        self._access_token = None

    def get(self, path: str) -> Optional[Any]:
        """讀取指定 Firestore 路徑的單一文件。

        優先嘗試遠端，失敗時回退到快取。

        Args:
            path: Firestore 文件路徑 (e.g. "localnews/Recent50LocalNews1")。

        Returns:
            文件資料 dict，或 None。
        """
        if self._project_id:
            try:
                return self._fetch_document(path)
            except Exception as e:
                logger.warning("Firestore fetch failed for %s: %s", path, e)

        return self._cache.get(path)

    def get_collection(
        self,
        collection_path: str,
        page_size: int = 100,
        order_by: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """讀取 Firestore 子集合的所有文件。

        Args:
            collection_path: 集合路徑 (e.g. "localnews/Recent50LocalNews1/news")。
            page_size: 每頁數量。
            order_by: 排序欄位（可選）。

        Returns:
            文件 list，每筆含 _id 欄位。
        """
        if not self._project_id:
            cached = self._cache.get(collection_path)
            if isinstance(cached, dict):
                return [{"_id": k, **v} for k, v in cached.items()]
            return []

        documents: List[Dict[str, Any]] = []
        page_token: Optional[str] = None

        while True:
            try:
                batch, page_token = self._fetch_collection_page(
                    collection_path, page_size, page_token, order_by,
                )
                documents.extend(batch)
                if not page_token:
                    break
            except Exception as e:
                logger.warning("Firestore collection fetch failed: %s", e)
                break

        logger.info("Fetched %d documents from %s", len(documents), collection_path)
        return documents

    def _fetch_document(self, path: str) -> Dict[str, Any]:
        """透過 Firestore REST API 讀取單一文件。"""
        url = (
            f"{_FIRESTORE_BASE}/projects/{self._project_id}"
            f"/databases/{self._database}/documents/{path}"
        )
        return self._firestore_get(url)

    def _fetch_collection_page(
        self,
        collection_path: str,
        page_size: int,
        page_token: Optional[str],
        order_by: Optional[str],
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """讀取集合的一頁文件。"""
        url = (
            f"{_FIRESTORE_BASE}/projects/{self._project_id}"
            f"/databases/{self._database}/documents/{collection_path}"
            f"?pageSize={page_size}"
        )
        if page_token:
            url += f"&pageToken={page_token}"
        if order_by:
            url += f"&orderBy={order_by}"

        token = self._ensure_token()
        req = Request(url, method="GET")
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Accept", "application/json")

        try:
            with urlopen(req, timeout=self._timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except Exception:
            self._invalidate_token()
            raise

        docs: List[Dict[str, Any]] = []
        for raw_doc in body.get("documents", []):
            doc_id = raw_doc["name"].rsplit("/", 1)[-1]
            parsed = _parse_firestore_doc(raw_doc)
            parsed["_id"] = doc_id
            docs.append(parsed)

        return docs, body.get("nextPageToken")

    def _firestore_get(self, url: str) -> Dict[str, Any]:
        """發送 authenticated GET request 到 Firestore REST API。"""
        token = self._ensure_token()
        req = Request(url, method="GET")
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Accept", "application/json")

        try:
            with urlopen(req, timeout=self._timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except Exception:
            self._invalidate_token()
            raise

        return _parse_firestore_doc(body)

    def get_trending_boost(self, title: str) -> Tuple[float, List[str]]:
        """計算標題的 Firebase trending 加分。

        比對 trending items 中的標題關鍵字，給予加分。

        Args:
            title: 新聞標題。

        Returns:
            (boost, matched_ids) 元組。
        """
        if not self._trending_items:
            return 0.0, []

        matched: List[FirebaseTrendingItem] = []
        for item in self._trending_items:
            # 雙向比對：trending title 包含在 news title 中，或反之
            if item.title and (
                item.title in title or title in item.title
                or self._keyword_overlap(item.title, title)
            ):
                matched.append(item)

        if not matched:
            return 0.0, []

        # 取最高的 boost，遞減加疊
        total_boost = 0.0
        matched_ids: List[str] = []
        for i, item in enumerate(matched[:3]):  # 最多取 3 個
            multiplier = 1.0 / (1.0 + i * 0.5)
            total_boost += item.boost * multiplier
            matched_ids.append(item.headline_id)

        return min(total_boost, 30.0), matched_ids

    @staticmethod
    def _keyword_overlap(text_a: str, text_b: str, min_overlap: int = 2) -> bool:
        """判斷兩段文字是否有足夠的關鍵字重疊。

        以 2-4 字中文子串做簡單比對。

        Args:
            text_a: 第一段文字。
            text_b: 第二段文字。
            min_overlap: 最少重疊子串數。

        Returns:
            是否有足夠重疊。
        """
        # 取 text_a 的 2-char ngrams
        ngrams_a = {text_a[i:i + 2] for i in range(len(text_a) - 1) if len(text_a[i:i + 2]) == 2}
        if not ngrams_a:
            return False

        overlap = sum(1 for ng in ngrams_a if ng in text_b)
        return overlap >= min_overlap

    def get_config(self, key: str, default: Any = None) -> Any:
        """讀取 Firebase 中的 scorer 設定。

        Args:
            key: 設定鍵名 (e.g. "threshold_override")。
            default: 預設值。

        Returns:
            設定值。
        """
        config = self.get("config/scorer")
        if isinstance(config, dict):
            return config.get(key, default)
        return default

    def refresh_trending(self, collection_path: str = "trending/headlines") -> int:
        """從 Firestore 遠端重新載入 trending 資料。

        Args:
            collection_path: Firestore 中 trending 資料的集合路徑。

        Returns:
            載入的 trending items 數量。
        """
        if not self._project_id:
            logger.warning("Cannot refresh trending: no project_id configured")
            return 0

        try:
            docs = self.get_collection(collection_path)
            if docs:
                data = {doc.get("_id", str(i)): doc for i, doc in enumerate(docs)}
                self._cache["trending/headlines"] = data
                self._parse_trending()
                logger.info("Refreshed %d trending items from Firestore", len(self._trending_items))
                return len(self._trending_items)
        except Exception as e:
            logger.warning("Failed to refresh trending from Firestore: %s", e)

        return 0
