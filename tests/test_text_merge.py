import pytest

from thundertalk.core.text_merge import merge_preview_terms


@pytest.mark.parametrize("final,preview,expected", [
    ("GPT的阿修罗、露娜、whatever、terra", "GPT 的 Astra、Luna、whatever、Terra",
     "GPT的Astra、Luna、whatever、terra"),
    ("今天使用露娜模型。", "今天使用Luna模型。", "今天使用Luna模型。"),
    ("今天使用露娜模型。", "今天使用 Astra 模型！", "今天使用Astra模型。"),
    ("今天使用克劳德奥普斯模型。", "今天使用Claude Opus模型。", "今天使用Claude Opus模型。"),
    ("我们选择吉皮提模型进行测试。", "我们选择GPT-6.1模型进行测试。", "我们选择GPT-6.1模型进行测试。"),
    ("现在启用回退模式进行测试。", "现在启用fallback模式进行测试。", "现在启用fallback模式进行测试。"),
    ("今天使用Luna模型。", "今天使用Astra模型。", "今天使用Luna模型。"),
    ("我们使用GPT-6.1模型。", "我们使用GPT-5.2模型！", "我们使用GPT-6.1模型。"),
    ("今天使用六点一模型。", "今天使用6.1模型。", "今天使用六点一模型。"),
    ("今天使用模型6.1测试。", "今天使用GPT-6.1测试。", "今天使用模型6.1测试。"),
    ("今天有20个模型。", "今天有30个模型。", "今天有20个模型。"),
    ("这是正确的中文。", "这是错误的中文！", "这是正确的中文。"),
    ("Use Claude Opus, please.", "Use Astra please!", "Use Claude Opus, please."),
    ("模型是阿修罗。", "fallback 完全不同 GPT!", "模型是阿修罗。"),
    ("今天使用露娜模型，然后介绍Terra。", "今天使用Luna模型", "今天使用露娜模型，然后介绍Terra。"),
    ("露娜", "Luna", "露娜"),  # no anchors
    ("露娜非常适合今天的测试。", "Luna非常适合今天的测试。", "露娜非常适合今天的测试。"),
    ("今天使用露娜", "今天使用Luna", "今天使用露娜"),  # open tail
    ("今天使用露娜，模型很好。", "今天使用Luna模型很好。", "今天使用露娜，模型很好。"),
    ("今天使用露娜，模型很好。", "今天使用Luna,模型很好。", "今天使用Luna，模型很好。"),
    ("今天使用露 娜模型。", "今天使用Luna模型。", "今天使用露 娜模型。"),
    ("今天使用露娜模型。", None, "今天使用露娜模型。"),
    ("今天使用露娜模型。", "", "今天使用露娜模型。"),
    ("", "今天使用Luna模型。", ""),
])
def test_conservative_merge(final, preview, expected):
    assert merge_preview_terms(final, preview) == expected


def test_identity_and_idempotence():
    final = "GPT的阿修罗、露娜、whatever、terra"
    preview = "GPT 的 Astra、Luna、whatever、Terra"
    merged = merge_preview_terms(final, preview)
    assert merge_preview_terms(merged, preview) == merged
    assert merge_preview_terms(final, final) == final
