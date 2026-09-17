# -*- coding: utf-8 -*-
"""
通用控件：字段多选列表、进度提示等。
"""

import os
import sys

import numpy as _np

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QLabel,
    QProgressBar,
    QGroupBox,
    QFileDialog,
    QLineEdit,
    QAbstractItemView,
    QFrame,
    QGraphicsDropShadowEffect,
    QCheckBox,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QImage, QPixmap


def _make_checkable_item(text: str) -> QListWidgetItem:
    item = QListWidgetItem(text)
    item.setFlags(
        item.flags()
        | Qt.ItemFlag.ItemIsUserCheckable
        | Qt.ItemFlag.ItemIsEnabled
        | Qt.ItemFlag.ItemIsSelectable
    )
    item.setCheckState(Qt.CheckState.Unchecked)
    return item


class ColumnSelector(QWidget):
    """双列表：可选列 <-> 已选列，用于配置对比字段、键字段。

    使用 Qt 原生 item checkState 实现勾选（默认 LTR：勾选框在左、文字紧随右侧），
    点击整行即勾选；「添加→ / ←移除」按钮按勾选项一键批量移动。
    """

    selection_changed = pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        self.available = QListWidget()
        self.available.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.available.itemClicked.connect(self._on_item_clicked)
        self.selected = QListWidget()
        self.selected.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.selected.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(QLabel("可选列"))
        layout.addWidget(self.available, 1)
        btn_layout = QVBoxLayout()
        add_btn = QPushButton("添加 →")
        add_btn.clicked.connect(self._add)
        remove_btn = QPushButton("← 移除")
        remove_btn.clicked.connect(self._remove)
        btn_layout.addWidget(add_btn)
        btn_layout.addWidget(remove_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)
        layout.addWidget(QLabel("已选列"))
        layout.addWidget(self.selected, 1)

    @staticmethod
    def _on_item_clicked(item: QListWidgetItem):
        """点击整行即切换勾选状态。"""
        item.setCheckState(
            Qt.CheckState.Unchecked
            if item.checkState() == Qt.CheckState.Checked
            else Qt.CheckState.Checked
        )

    def set_columns(self, columns: list):
        """设置可选列（清空已选并填充可选）。"""
        self.available.clear()
        self.selected.clear()
        for c in columns:
            self.available.addItem(_make_checkable_item(str(c)))

    def _add(self):
        """把可选列中已勾选的项移到已选列，并取消勾选。"""
        for i in range(self.available.count() - 1, -1, -1):
            item = self.available.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                self.available.takeItem(i)
                new_item = _make_checkable_item(item.text())
                self.selected.addItem(new_item)
        self._emit()

    def _remove(self):
        """把已选列中已勾选的项移回可选列，并取消勾选。"""
        for i in range(self.selected.count() - 1, -1, -1):
            item = self.selected.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                self.selected.takeItem(i)
                new_item = _make_checkable_item(item.text())
                self.available.addItem(new_item)
        self._emit()

    def _emit(self):
        self.selection_changed.emit(self.get_selected())

    def get_selected(self) -> list:
        return [self.selected.item(i).text() for i in range(self.selected.count())]


class FilePathRow(QWidget):
    """单行：标签 + 路径输入框 + 浏览按钮。"""

    path_changed = pyqtSignal(str)

    def __init__(self, label: str = "文件", parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setSpacing(8)
        self._label = QLabel(label)
        self._label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._label.setMinimumWidth(90)
        layout.addWidget(self._label)
        self.line = QLineEdit()
        self.line.setReadOnly(True)
        # 防止窗口缩放 / 高 DPI 下输入框被挤压成一条细线
        self.line.setMinimumWidth(240)
        layout.addWidget(self.line, 1)
        btn = QPushButton("浏览...")
        btn.clicked.connect(self._browse)
        layout.addWidget(btn)

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择文件",
            "",
            "支持格式 (*.xlsx *.xls *.csv *.tsv *.docx *.pdf);;所有 (*.*)",
        )
        if path:
            self.line.setText(path)
            self.path_changed.emit(path)

    def path(self) -> str:
        return self.line.text().strip()

    def set_path(self, path: str):
        self.line.setText(path or "")


class ProgressWidget(QWidget):
    """进度条 + 状态文本。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self.label = QLabel("就绪")
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        layout.addWidget(self.label)
        layout.addWidget(self.bar)

    def set_progress(self, value: int, text: str = ""):
        self.bar.setValue(min(100, max(0, value)))
        if text:
            self.label.setText(text)

    def set_busy(self, text: str = "处理中..."):
        self.bar.setRange(0, 0)  # 不确定进度
        self.label.setText(text)

    def set_idle(self, text: str = "就绪"):
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.label.setText(text)


# ---------------------------------------------------------------------------
# 顶部品牌条：Logo + 名称 + 分隔符 + 标语
# ---------------------------------------------------------------------------
_HEADER_GRAY = "#eaecef"          # 顶部品牌条底色（与页面浅灰底接近）
_LOGO_HEIGHT = 36                 # Logo 显示高度（等比）
_NAME_TEXT = "CGN 核岛系统所"
_SEPARATOR_TEXT = "|"
_SLOGAN_TEXT = "携手核岛，点亮未来"


def _asset_path(name: str) -> str:
    """frozen 感知的资源路径（assets/logo.png）。"""
    if getattr(sys, "frozen", False):
        # onefile 模式：资源解压到 sys._MEIPASS
        if hasattr(sys, "_MEIPASS"):
            root = sys._MEIPASS
        else:
            # onedir 模式：资源在 exe 旁 _internal 目录（兼容旧布局的回退到 exe 同级）
            root = os.path.join(os.path.dirname(sys.executable), "_internal")
            if not os.path.isdir(os.path.join(root, "assets")):
                root = os.path.dirname(sys.executable)
    else:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "assets", name)


def _recolor_logo(image_path: str, gray: str = _HEADER_GRAY) -> QPixmap:
    """把 Logo 画布白/透明底替换为 gray，保留图形细节，并裁剪到内容边界。

    Logo 为“橙色 GD 标 + 图形内白色细线”。策略：
    - 背景：远离橙色区域的纯白 / 透明像素 -> 置为 gray（融入浅灰横条）
    - 细节：紧邻橙色的白像素（图形内细线）保留为白，避免细节被抹掉
    - 剔除透明，避免灰条下方漏出白底
    """
    img = QImage(image_path)
    if img.isNull():
        return QPixmap()
    img = img.convertToFormat(QImage.Format.Format_ARGB32)  # 内存字节序: B,G,R,A
    h, w = img.height(), img.width()

    g = QColor(gray)
    gr, gg, gb = g.red(), g.green(), g.blue()

    ptr = img.constBits()
    ptr.setsize(img.sizeInBytes())
    arr = _np.frombuffer(ptr, dtype=_np.uint8).reshape((h, w, 4)).copy()
    B = arr[:, :, 0].astype(_np.int16)
    G = arr[:, :, 1].astype(_np.int16)
    R = arr[:, :, 2].astype(_np.int16)
    A = arr[:, :, 3]

    # 前景橙色掩码
    orange = (R - G > 40) & (R - B > 40) & (R > 120)
    # 白像素（画布或细节），仅计不透明
    white = (R > 230) & (G > 230) & (B > 230) & (A > 0)

    # 以橙为中心膨胀 2px，判断“贴近图形”的白=细节
    near = orange
    for dy in range(-2, 3):
        for dx in range(-2, 3):
            near = near | _np.roll(orange, (dy, dx), axis=(0, 1))

    white_detail = white & near                  # 图形内细线 -> 保留白
    white_bg = white & (~near)                   # 远处画布 -> 变灰
    transparent = A < 250

    arr[white_bg, 0] = gb
    arr[white_bg, 1] = gg
    arr[white_bg, 2] = gr
    arr[transparent, 0] = gb                     # 透明底 -> 灰
    arr[transparent, 1] = gg
    arr[transparent, 2] = gr
    arr[transparent, 3] = 255

    # 裁剪到内容边界（橙色+细节白），得到贴近图形的图
    content = orange | white_detail
    if content.any():
        ys, xs = _np.where(content)
        pad = 2
        y0, y1 = max(0, int(ys.min()) - pad), min(h - 1, int(ys.max()) + pad)
        x0, x1 = max(0, int(xs.min()) - pad), min(w - 1, int(xs.max()) + pad)
        # 切成连续数组，保证 bytesPerLine = 宽度×4，避免像素错位
        arr = _np.ascontiguousarray(arr[y0:y1 + 1, x0:x1 + 1])

    ch, cw = arr.shape[0], arr.shape[1]
    out = QImage(arr.tobytes(), cw, ch, arr.strides[0], QImage.Format.Format_ARGB32).copy()
    return QPixmap.fromImage(out)


def _set_label_text(label: QLabel, text: str, size: int, color: str, weight=QFont.Weight.Normal):
    """按规格设置文字标签：字体、字号、字重、颜色。

    注意：全局 QSS 中设置了 font-size，因此必须把字号也写进标签自身的样式表，
    否则会被全局 font-size:12px 覆盖，导致字号设置不生效。
    """
    f = QFont("Microsoft YaHei")
    f.setPixelSize(size)
    f.setWeight(weight)
    label.setFont(f)
    label.setStyleSheet(f"color: {color}; background: transparent; font-size: {size}px;")


class HeaderWidget(QWidget):
    """顶部品牌横条：浅灰底 + Logo(白底换灰) + 名称 + 分隔符 + 标语。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("appHeader")
        self.setFixedHeight(50)
        self.setStyleSheet('#appHeader { background-color: ' + _HEADER_GRAY + '; }')

        lay = QHBoxLayout(self)
        # 文字左右预留 8px 空白
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(0)

        # 左：Logo（白底已换灰，等比按高显示）
        self.logo_label = QLabel()
        self.logo_label.setFixedHeight(_LOGO_HEIGHT)
        logo_pm = _recolor_logo(_asset_path("logo.png"))
        if not logo_pm.isNull():
            self.logo_label.setPixmap(
                logo_pm.scaledToHeight(_LOGO_HEIGHT, Qt.TransformationMode.SmoothTransformation)
            )
        lay.addWidget(self.logo_label, 0, Qt.AlignmentFlag.AlignVCenter)

        # Logo 与 名称 之间 10px
        lay.addSpacing(10)

        # 名称：微软雅黑加粗深灰（比标语大一号）
        self.name_label = QLabel(_NAME_TEXT)
        _set_label_text(self.name_label, _NAME_TEXT, 22, "#333333", QFont.Weight.DemiBold)
        lay.addWidget(self.name_label, 0, Qt.AlignmentFlag.AlignVCenter)

        # 主名称 与 分隔符 之间 6px
        lay.addSpacing(6)

        # 分隔符：14px 浅灰
        self.separator_label = QLabel(_SEPARATOR_TEXT)
        _set_label_text(self.separator_label, _SEPARATOR_TEXT, 14, "#999999")
        lay.addWidget(self.separator_label, 0, Qt.AlignmentFlag.AlignVCenter)

        # 分隔符 与 标语 之间 6px
        lay.addSpacing(6)

        # 标语：微软雅黑 12px、常规字重、中度浅灰
        self.slogan_label = QLabel(_SLOGAN_TEXT)
        _set_label_text(self.slogan_label, _SLOGAN_TEXT, 12, "#4b5563")
        lay.addWidget(self.slogan_label, 0, Qt.AlignmentFlag.AlignVCenter)

        lay.addStretch(1)


# ---------------------------------------------------------------------------
# 统一样式助手：纯白模块卡片 + 页眉标题 + 文案层级（工业软件风格）
# 视觉分层：页面浅灰底(#f4f5f7) → 模块纯白卡片(#fff) → 组内控件/输入浅灰(#fafafa)
# ---------------------------------------------------------------------------
PAGE_BG_COLOR = "#eceef1"
CARD_WHITE = "#FFFFFF"
CARD_BORDER = "#d8dee5"        # 卡片边框 / 内部细分隔线（较浅的可见实线）
CARD_TITLE_COLOR = "#1f2937"   # 卡片标题（深灰加粗）
BODY_TEXT = "#2f3340"          # 普通正文（深色，保证对比度）
HINT_GRAY = "#6b7280"          # 辅助说明浅灰
FIELD_BG = "#fafafa"           # 组内输入/控件浅灰底
ACCENT = "#2563eb"             # 强调色（选中态/焦点）

# 卡片本体：白底、细边框、圆角（阴影用 QGraphicsDropShadowEffect 实现）
_CARD_QSS = (
    "QGroupBox#card {"
    "background-color: #ffffff;"
    "border: 1px solid #e5e7eb;"
    "border-radius: 6px;"
    "}"
)


def _add_card_shadow(g) -> None:
    """给卡片叠加轻微浅阴影 0 1px 4px rgba(0,0,0,0.08)，与浅灰底剥离。"""
    eff = QGraphicsDropShadowEffect(g)
    eff.setBlurRadius(4)
    eff.setXOffset(0)
    eff.setYOffset(1)
    eff.setColor(QColor(0, 0, 0, 20))
    g.setGraphicsEffect(eff)


def style_group_card(g, white: bool = False) -> None:
    """把 QGroupBox 渲染为带内置标题的白色模块卡片（用于折叠组等保留 Qt 标题的场景）。

    默认白色卡片；传 white=True 时仍为白底（本就一致）。浅阴影 + 圆角使卡片从灰底剥离。
    """
    g.setObjectName("card")
    g.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    g.setStyleSheet(_CARD_QSS)
    _add_card_shadow(g)


def card_group(title: str, parent=None):
    """创建「白卡片 + 页眉标题 + 分隔线 + 内容区」的模块卡片。

    标题移入卡片内部作为页眉（深灰加粗、略大于正文），下方加细分隔线。
    返回 (groupbox, content_layout)；内容追加到 content_layout 即位于分隔线之下。
    需传给一个具名布局变量（如 fl），以便后续 addWidget。
    """
    g = QGroupBox("", parent)
    style_group_card(g)
    lay = QVBoxLayout(g)
    lay.setContentsMargins(16, 12, 16, 16)
    lay.setSpacing(8)
    # 页眉标题
    title_lbl = QLabel(title)
    title_lbl.setStyleSheet(
        f"color: {CARD_TITLE_COLOR}; font-weight: 600; font-size: 14px;"
        f"background: transparent; padding: 0 0 8px 0;"
    )
    lay.addWidget(title_lbl)
    # 标题下方的细分隔线
    hline = QFrame()
    hline.setFixedHeight(1)
    hline.setStyleSheet(f"background: {CARD_BORDER}; border: none;")
    lay.addWidget(hline)
    return g, lay


def make_hint(text: str) -> QLabel:
    """辅助说明文字：更小字号(11px) + 浅灰 + 启用换行（最多 2 行精简描述）。"""
    h = QLabel(text)
    h.setWordWrap(True)
    f = QFont("Microsoft YaHei")
    f.setPixelSize(11)
    h.setFont(f)
    h.setStyleSheet(f"color: {HINT_GRAY}; background: transparent; font-size: 11px;")
    return h


def card_padding(layout) -> None:
    """给分组卡片内部布局设置统一的内边距与行间距。"""
    layout.setContentsMargins(16, 12, 16, 16)
    layout.setSpacing(8)


# ---------------------------------------------------------------------------
# 应用级全局样式：页面底色 / Tab 选中态 / 组内浅灰输入 / 空态虚线边框
# ---------------------------------------------------------------------------
def apply_app_style(app) -> None:
    # 计算 assets 绝对路径，供 QSS image: url() 引用（打包后相对路径无效）
    _assets_dir = _asset_path("arrow_up.svg").replace("\\", "/")
    _arrow_up = _assets_dir
    _arrow_down = _asset_path("arrow_down.svg").replace("\\", "/")
    app.setStyleSheet(
        f"""
        * {{ outline: 0; }}
        QWidget {{
            color: {BODY_TEXT};
            font-family: "Microsoft YaHei", "Microsoft YaHei UI";
            font-size: 13px;
        }}
        /* 页面浅灰底 + 去除 Tab 容器边框 */
        QMainWindow, QDialog, QTabWidget::pane {{ background-color: {PAGE_BG_COLOR}; }}
        QTabWidget::pane {{ border: none; }}
        QTabWidget > QWidget {{ background-color: {PAGE_BG_COLOR}; }}
        /* Tab 入口标签：加边框（chips）+ 选中态加深加粗 */
        QTabBar::tab {{
            background: transparent; color: {BODY_TEXT};
            border: 1px solid {CARD_BORDER}; border-radius: 6px;
            padding: 6px 16px; margin: 2px 3px;
        }}
        QTabBar::tab:hover:!selected {{ border-color: #b6bec8; color: {CARD_TITLE_COLOR}; }}
        QTabBar::tab:selected {{ color: {CARD_TITLE_COLOR}; font-weight: 600;
            background-color: #ffffff; border: 1px solid #9aa4b0;
            border-bottom: 2px solid {ACCENT}; }}
        /* 组内控件/输入：极浅灰底，形成「内白→组浅灰」三级分层 */
        QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit, QDateTimeEdit,
        QTimeEdit, QTextEdit, QPlainTextEdit {{
            background-color: {FIELD_BG};
            border: 1px solid {CARD_BORDER}; border-radius: 4px;
            padding: 4px 8px; selection-background-color: {ACCENT}; color: {BODY_TEXT};
        }}
        QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
        QDateEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{
            border: 1px solid {ACCENT};
        }}
        /* SpinBox 上下按钮：左右并排，显式箭头符号，加宽确保可点击 */
        QSpinBox::up-button, QDoubleSpinBox::up-button,
        QDateEdit::up-button, QDateTimeEdit::up-button, QTimeEdit::up-button {{
            subcontrol-origin: border; subcontrol-position: center right;
            width: 24px; height: 100%; border: none;
            border-left: 1px solid {CARD_BORDER}; border-radius: 0; background: #eef0f3;
        }}
        QSpinBox::down-button, QDoubleSpinBox::down-button,
        QDateEdit::down-button, QDateTimeEdit::down-button, QTimeEdit::down-button {{
            subcontrol-origin: border; subcontrol-position: center right;
            width: 24px; height: 100%; border: none;
            border-left: 1px solid {CARD_BORDER}; border-radius: 0; background: #eef0f3;
            margin-right: 24px;
        }}
        QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
        QDateEdit::up-button:hover, QDateTimeEdit::up-button:hover, QTimeEdit::up-button:hover {{
            background: #dde2e8;
        }}
        QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover,
        QDateEdit::down-button:hover, QDateTimeEdit::down-button:hover, QTimeEdit::down-button:hover {{
            background: #dde2e8;
        }}
        /* 箭头符号：引用 SVG 箭头图片，确保可见 */
        QSpinBox::up-arrow, QDoubleSpinBox::up-arrow,
        QDateEdit::up-arrow, QDateTimeEdit::up-arrow, QTimeEdit::up-arrow {{
            width: 14px; height: 10px;
            image: url({_arrow_up});
        }}
        QSpinBox::down-arrow, QDoubleSpinBox::down-arrow,
        QDateEdit::down-arrow, QDateTimeEdit::down-arrow, QTimeEdit::down-arrow {{
            width: 14px; height: 10px;
            image: url({_arrow_down});
        }}
        QPushButton {{
            background-color: #ffffff; color: {BODY_TEXT};
            border: 1px solid {CARD_BORDER}; border-radius: 4px;
            padding: 5px 14px;
        }}
        QPushButton:hover {{ background-color: #f3f4f6; border: 1px solid #d1d5db; }}
        QPushButton:focus {{ border: 1px solid {ACCENT}; }}
        /* 空状态列表/选择区：较深的实线边框 */
        QListWidget, QListView {{
            border: 1px solid #aab3bd; border-radius: 4px;
            background-color: #ffffff; padding: 2px;
        }}
        QListWidget::item {{ padding: 4px 6px; }}
        QListWidget::item:selected {{ background-color: {ACCENT}; color: #ffffff; }}
        QTreeWidget, QTreeView {{ border: 1px solid {CARD_BORDER}; background-color: #ffffff; }}
        QTreeWidget::item:selected {{ background-color: {ACCENT}; color: #ffffff; }}
        QCheckBox, QRadioButton {{ spacing: 6px; }}
        QScrollBar:vertical {{ width: 10px; background: transparent; }}
        QScrollBar::handle:vertical {{ background: #cbd5e1; border-radius: 4px; min-height: 24px; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """
    )
