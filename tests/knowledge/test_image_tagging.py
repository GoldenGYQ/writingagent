from nanobot.knowledge.image_tagging import classify_knowledge_image


def test_labels_special_equipment_license_and_company() -> None:
    result = classify_knowledge_image(
        text=(
            "中华人民共和国特种设备生产许可证\n"
            "单位名称：大连冰山集团工程有限公司\n许可项目：工业管道安装"
        ),
        confidence=0.98,
    )

    assert result["document_type"] == "special_equipment_license"
    assert "企业资质" in result["tags"]
    assert "大连冰山集团工程有限公司" in result["entities"]
    assert result["review_status"] == "candidate"


def test_empty_ocr_is_kept_as_vision_candidate() -> None:
    result = classify_knowledge_image(text="", confidence=None)

    assert result["document_type"] == "visual_asset"
    assert result["requires_vision"] is True
    assert result["review_status"] == "needs_review"


def test_sensitive_financial_document_is_marked() -> None:
    result = classify_knowledge_image(
        text="基本存款账户信息 开户银行 账户号码",
        confidence=0.95,
    )

    assert result["document_type"] == "bank_account"
    assert result["sensitive"] is True
