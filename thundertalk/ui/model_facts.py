"""Quiet, shared model facts for Models and the Studio engine picker."""

from PySide6.QtCore import QLocale
from PySide6.QtWidgets import QLabel, QWidget

from thundertalk.core import i18n
from thundertalk.core.i18n import t
from thundertalk.ui import theme
from thundertalk.ui.widgets import FlowLayout

# Native labels stay recognisable in either interface language.
_NAMES = {
    "zh": ("Chinese", "中文"),
    "cmn": ("Chinese", "中文"),
    "cmn_Hant": ("Chinese (Traditional)", "繁體中文"),
    "en": ("English", "English"),
    "eng": ("English", "English"),
    "ja": ("日本語", "日本語"),
    "jpn": ("日本語", "日本語"),
    "ko": ("한국어", "한국어"),
    "kor": ("한국어", "한국어"),
    "yue": ("Cantonese", "粤语"),
    "fil": ("Filipino", "菲律宾语"),
}


def language_name(code):
    if code in _NAMES:
        return _NAMES[code][i18n.LANG == "zh"]
    locale = QLocale(code.replace("_Hant", "_TW"))
    name = locale.nativeLanguageName()
    return (
        f"{name} ({code})" if name and locale.language() != QLocale.Language.C else code
    )


def facts_text(
    params, size_mb, cpu, speed="unmeasured", *, backbone=False, cpu_gpu=False
):
    size = f"{size_mb / 1000:.1f} GB" if size_mb >= 1000 else f"{size_mb} MB"
    count = (
        t("models.backbone_params")
        if backbone
        else t("models.params").format(n=params)
        if params
        else t("models.params_unknown")
    )
    return " · ".join(
        (
            count,
            size,
            t("models.cpu_gpu" if cpu_gpu else "models.cpu" if cpu else "models.gpu"),
            t(f"models.speed.{speed}"),
        )
    )


def language_tags(
    codes,
    *,
    complete=True,
    hotwords=False,
    speakers=False,
    clone=False,
    timestamps=False,
):
    host = QWidget()
    flow = FlowLayout(host, 6, 6)
    labels = [language_name(code) for code in codes]
    tooltip = ", ".join(labels)
    if not complete:
        tooltip = t("models.partial_languages") + "\n" + tooltip
    for label in labels[:4]:
        flow.addWidget(theme.badge(label, "muted"))
    if len(labels) > 4 or not complete:
        more = theme.badge(f"+{len(labels) - 4}" if complete else "50+", "muted")
        more.setToolTip(tooltip)
        more.setAccessibleName(tooltip)
        flow.addWidget(more)
    if not {"zh", "cmn", "cmn_Hant"}.intersection(codes):
        flow.addWidget(theme.badge(t("models.no_chinese"), "muted"))
    for on, key in (
        (hotwords, "hotwords"),
        (speakers, "speakers"),
        (clone, "clone"),
        (timestamps, "timestamps"),
    ):
        if on:
            flow.addWidget(theme.badge(t(f"models.feature.{key}"), "neutral"))
    host.setToolTip(tooltip)
    return host


def facts_label(text):
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet(
        f"color: {theme.TEXT_SECONDARY}; font-size: 12px; background: transparent;"
    )
    return label
