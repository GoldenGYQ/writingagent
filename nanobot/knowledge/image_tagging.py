"""Deterministic first-pass labels for extracted Knowledge image assets.

The labels help an extraction agent choose useful evidence without pretending
that OCR or filename heuristics are authoritative.  Every result therefore
records its method and review state, and image-only assets remain explicit
vision candidates.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, cast

_COMPANY_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9（）()·]{4,40}(?:股份)?有限公司")

_DOCUMENT_RULES: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("bank_account", ("基本存款账户", "开户银行", "账户号码"), ("银行账户", "企业证明")),
    ("business_license", ("营业执照", "统一社会信用代码"), ("营业执照", "企业资质")),
    ("special_equipment_license", ("特种设备生产许可证", "许可项目", "许可子项目"), ("生产许可", "企业资质")),
    ("certificate", ("认证证书", "certificate", "管理体系"), ("认证证书", "企业资质")),
    ("invoice", ("发票", "价税合计", "购买方", "销售方"), ("发票", "财务凭证")),
    ("receipt", ("收据", "付款", "人民币"), ("票据", "财务凭证")),
    ("contract", ("合同", "甲方", "乙方"), ("合同", "业绩证明")),
    ("identity_document", ("居民身份证", "公民身份号码"), ("身份证明", "敏感材料")),
    ("authorization", ("授权委托书", "法定代表人", "委托代理人"), ("授权文件", "企业证明")),
    ("test_report", ("检验报告", "检测报告", "检验检测"), ("检测报告", "产品证明")),
)

_SENSITIVE_TYPES = {"bank_account", "invoice", "receipt", "identity_document"}


def _strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        str(item).strip()
        for item in cast(list[object], value)
        if str(item).strip()
    ]


def classify_knowledge_image(
    *,
    text: str,
    confidence: float | None,
    asset: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return conservative OCR-derived labels for one image asset."""

    normalized = text.casefold().strip()
    document_type = "unclassified"
    tags: list[str] = []
    for candidate, needles, candidate_tags in _DOCUMENT_RULES:
        if any(needle.casefold() in normalized for needle in needles):
            document_type = candidate
            tags.extend(candidate_tags)
            break

    requires_vision = not bool(normalized)
    if requires_vision:
        document_type = "visual_asset"
        tags.extend(("待视觉识别", "图片证据"))
    else:
        tags.append("OCR文本")

    entities = sorted(set(_COMPANY_RE.findall(text)))[:12]
    tags.extend(entities[:3])
    prior_tags = _strings((asset or {}).get("tags"))
    tags.extend(prior_tags)
    unique_tags = list(dict.fromkeys(tag for tag in tags if tag))
    low_confidence = confidence is None or confidence < 0.80
    return {
        "document_type": document_type,
        "tags": unique_tags,
        "entities": entities,
        "sensitive": document_type in _SENSITIVE_TYPES,
        "requires_vision": requires_vision,
        "review_status": "needs_review" if requires_vision or low_confidence else "candidate",
        "label_method": "ocr_rules_v1",
    }
