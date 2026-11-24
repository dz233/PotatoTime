"""四个主要页面：今日、专注、历史、设置。"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta
import json
from pathlib import Path

from PySide6.QtCore import QDate, Qt, QTimer
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDateEdit,
    QDialog, QFileDialog, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
    QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSpinBox,
    QDoubleSpinBox, QStackedWidget, QSystemTrayIcon, QTableWidget, QTableWidgetItem,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget, QLineEdit)

from .audio import EVENTS
from .synthesis import DEFAULT_PRESETS, PRESETS
from .core import Config, LABELS, day_seconds, minutes, now, parse
from .allocation import assignments, effective_before, references, task_title
from .widgets import DesktopNotice, Form, Ring, HeightHandle, icon, theme
from . import __version__


def label(text, name=None):
    widget = QLabel(text)
    if name:
        widget.setObjectName(name)
    widget.setWordWrap(True)
    return widget


def button(text, callback, primary=False):
    widget = QPushButton(text)
    if primary:
        widget.setObjectName("primary")
    widget.clicked.connect(callback)
    return widget


def card():
    box = QFrame()
    box.setObjectName("card")
    layout = QVBoxLayout(box)
    layout.setContentsMargins(20, 18, 20, 18)
    layout.setSpacing(12)
    return box, layout


def table(columns):
    widget = QTableWidget(0, len(columns))
    widget.setHorizontalHeaderLabels(columns)
    widget.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    widget.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    widget.verticalHeader().hide()
    widget.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    widget.setAlternatingRowColors(True)
    return widget


class MainWindow(QMainWindow):
    def __init__(self, controller, audio, no_tray=False, auto_tick=True):
        super().__init__()
        self.ctrl, self.store, self.audio = controller, controller.store, audio
        self.quitting = False
        self.dialogs = []
        self.summary_savers = []
        self.note_id = None
        self.note_pending = None
        self.day_pending = None
        self.loaded_day = None
        self.notice_dialog = None
        self.notification_popup = None
        self.notice_records = []
        self.mini = None
        self.tray = None
        self.selected_tasks = list(self.ctrl.task_ids)
        self.setWindowTitle(f"TomatoTime {__version__} · 专注与学习日志")
        self.setWindowIcon(icon())
        self.resize(1180, 840)
        self.setMinimumSize(980, 680)
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setSingleShot(True)
        self.refresh_timer.timeout.connect(self.refresh)
        self.note_timer = QTimer(self)
        self.note_timer.setSingleShot(True)
        self.note_timer.timeout.connect(self.flush_note)
        self.day_timer = QTimer(self)
        self.day_timer.setSingleShot(True)
        self.day_timer.timeout.connect(self.flush_day)

        root = QWidget()
        row = QHBoxLayout(root)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(175)
        navigation = QVBoxLayout(sidebar)
        navigation.setContentsMargins(12, 24, 12, 18)
        navigation.addWidget(label("TomatoTime", "subheading"))
        navigation.addWidget(label("专注，也留下一点收获", "muted"))
        navigation.addSpacing(24)
        self.pages = QStackedWidget()
        self.nav_buttons = []
        for index, text in enumerate(("今日", "专注", "历史", "设置")):
            item = QPushButton(text)
            item.setObjectName("nav")
            item.setCheckable(True)
            item.clicked.connect(lambda checked=False, i=index: self.navigate(i))
            navigation.addWidget(item)
            self.nav_buttons.append(item)
        navigation.addStretch()
        navigation.addWidget(button("迷你窗口", self.show_mini))
        self.pin = QCheckBox("始终置顶")
        self.pin.setChecked(self.store.get("pinned", False))
        self.pin.toggled.connect(self.set_pinned)
        navigation.addWidget(self.pin)
        navigation.addWidget(label("记录保存在本机", "muted"))
        row.addWidget(sidebar)
        row.addWidget(self.pages, 1)
        self.setCentralWidget(root)
        self.create_today()
        self.create_focus()
        self.create_history()
        self.create_settings()
        self.apply_theme()
        self.ctrl.changed.connect(lambda: self.refresh_timer.start(120))
        self.ctrl.phase_changed.connect(self.phase_changed)
        self.ctrl.note_ready.connect(self.bind_note)
        self.ctrl.session_finished.connect(self.show_session_summary)
        self.ctrl.notices.connect(self.show_notices)
        self.ctrl.message.connect(lambda text: self.statusBar().showMessage(text, 30000))
        self.audio.error.connect(lambda text: self.statusBar().showMessage(text, 15000))
        self.ctrl.sound.connect(self.audio.queue)
        self.tick_timer = QTimer(self)
        self.tick_timer.timeout.connect(self.tick)
        if auto_tick:
            self.tick_timer.start(250)
        self.tray = None
        if not no_tray and QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(icon(), self)
            self.tray.setToolTip("TomatoTime")
            menu = QMenu(self)
            menu.addAction("打开窗口", self.reveal)
            menu.addAction("暂停／继续", lambda: self.guard(self.toggle_pause))
            menu.addAction("查看提醒", self.manage_reminders)
            menu.addSeparator()
            menu.addAction("退出", self.exit_app)
            self.tray.setContextMenu(menu)
            self.tray.activated.connect(lambda reason: self.reveal() if reason == QSystemTrayIcon.ActivationReason.DoubleClick else None)
            self.tray.show()
        geometry = self.store.get("geometry")
        if geometry:
            self.restoreGeometry(bytes.fromhex(geometry))
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, self.pin.isChecked())
        self.refresh()
        self.phase_changed()
        self.navigate(0)
        if self.ctrl.recovery:
            self.navigate(1)
            self.statusBar().showMessage("发现未完成计划，已恢复为暂停状态；关闭期间未计入专注。可继续或结束。", 60000)
        if self.ctrl.last_note:
            self.bind_note(self.ctrl.last_note)

    def guard(self, action):
        try:
            return action()
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, "请检查", str(error))

    def page(self, title, subtitle):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(26, 24, 26, 24)
        layout.setSpacing(16)
        layout.addWidget(label(title, "heading"))
        layout.addWidget(label(subtitle, "muted"))
        scroll.setWidget(content)
        self.pages.addWidget(scroll)
        return layout

    def navigate(self, index):
        self.pages.setCurrentIndex(index)
        for i, item in enumerate(self.nav_buttons):
            item.setChecked(i == index)
        if index == 2:
            self.refresh_history()

    def create_today(self):
        layout = self.page("今天，准备做些什么？", "先写计划，再开始专注。时间自动记录，感想可以慢慢补。")
        toolbar = QHBoxLayout()
        self.day_picker = QDateEdit(QDate.currentDate())
        self.day_picker.setCalendarPopup(True)
        self.day_picker.setDisplayFormat("yyyy-MM-dd")
        self.day_picker.dateChanged.connect(self.change_day)
        toolbar.addWidget(self.day_picker)
        toolbar.addWidget(button("回到今天", lambda: self.day_picker.setDate(QDate.currentDate())))
        toolbar.addStretch()
        toolbar.addWidget(button("新建提醒", lambda: self.reminder_form()))
        toolbar.addWidget(button("管理提醒", self.manage_reminders))
        layout.addLayout(toolbar)
        stats_row = QHBoxLayout()
        self.stat_labels = []
        for title in ("实际专注", "完整番茄", "待办完成"):
            box, inner = card()
            inner.addWidget(label(title, "muted"))
            value = label("0", "stat")
            inner.addWidget(value)
            stats_row.addWidget(box)
            self.stat_labels.append(value)
        layout.addLayout(stats_row)
        box, inner = card()
        inner.addWidget(label("今日待办", "subheading"))
        self.tasks_table = table(("完成", "任务", "备注", "预计／当日投入"))
        self.tasks_table.setMinimumHeight(180)
        self.tasks_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.tasks_table.itemChanged.connect(self.task_checked)
        self.tasks_table.cellDoubleClicked.connect(lambda *_: self.edit_task())
        inner.addWidget(self.tasks_table)
        handle = HeightHandle(self.tasks_table)
        handle.resized.connect(lambda height: self.store.set("task_table_height", height))
        inner.addWidget(handle)
        actions = QHBoxLayout()
        for text, action in (("添加", lambda: self.edit_task(True)), ("编辑", self.edit_task),
                             ("开始专注", self.focus_task), ("上移", lambda: self.move_task(-1)),
                             ("下移", lambda: self.move_task(1)), ("带到明天", self.carry_task), ("删除", self.delete_task)):
            actions.addWidget(button(text, lambda checked=False, fn=action: self.guard(fn)))
        inner.addLayout(actions)
        layout.addWidget(box)
        box, inner = card()
        self.upcoming = label("暂无待处理提醒", "muted")
        inner.addWidget(self.upcoming)
        inner.addWidget(label("当天备注", "subheading"))
        self.day_note = QPlainTextEdit()
        self.day_note.setPlaceholderText("今天的安排、状态或临时想法……")
        self.day_note.setMaximumHeight(85)
        self.day_note.textChanged.connect(self.queue_day)
        inner.addWidget(self.day_note)
        layout.addWidget(box)
        box, inner = card()
        inner.addWidget(label("今天的时间轴", "subheading"))
        self.timeline = self.make_timeline()
        inner.addWidget(self.timeline)
        inner.addWidget(button("补记时间段", lambda: self.guard(self.manual_form)))
        layout.addWidget(box)
        box, inner = card()
        self.daily_summary = label("", "muted")
        inner.addWidget(label("当日总结", "subheading"))
        inner.addWidget(self.daily_summary)
        self.day_review = QPlainTextEdit()
        self.day_review.setPlaceholderText("今天学到了什么？明天准备怎样调整？（可选）")
        self.day_review.setMaximumHeight(100)
        self.day_review.textChanged.connect(self.queue_day)
        inner.addWidget(self.day_review)
        actions = QHBoxLayout()
        actions.addWidget(button("结束今天", self.close_day, True))
        actions.addWidget(button("导出当日日志", lambda: self.export_day(self.selected_day())))
        inner.addLayout(actions)
        layout.addWidget(box)

    def selected_day(self):
        return self.day_picker.date().toString("yyyy-MM-dd")

    def change_day(self, *_):
        self.flush_day()
        self.refresh()

    def queue_day(self):
        self.day_pending = (self.selected_day(), self.day_note.toPlainText(), self.day_review.toPlainText())
        self.day_timer.start(400)

    def flush_day(self):
        self.day_timer.stop()
        if self.day_pending:
            day, note, reflection = self.day_pending
            self.day_pending = None
            self.store.save_day(day, note=note, reflection=reflection, closed=0)

    def task_id(self):
        row = self.tasks_table.currentRow()
        return self.tasks_table.item(row, 0).data(Qt.ItemDataRole.UserRole) if row >= 0 else None

    def edit_task(self, new=False):
        identity = None if new else self.task_id()
        data = self.store.one("tasks", identity) if identity else None
        if not new and not data:
            return
        form = Form("添加待办" if new else "编辑待办", self)
        title = form.text("任务", data["title"] if data else "")
        notes = form.text("备注", data["notes"] if data else "", True)
        estimate = form.number("预计分钟（0 表示不填）", data["estimate"] / 60 if data else 0, decimal=True)
        unit = form.choice("估时单位", [("分钟", 60), ("小时", 3600), ("番茄（按当前专注时长）", self.focus_minutes.value() * 60)])
        state = form.choice("状态", [("未完成", "todo"), ("进行中", "doing"), ("已完成", "done")], data["status"] if data else "todo")
        if form.exec():
            def save():
                if not title.text().strip():
                    raise ValueError("请输入任务名称")
                if identity:
                    self.store.update("tasks", identity, title=title.text().strip(), notes=notes.toPlainText(), estimate=estimate.value() * unit.currentData(), status=state.currentData())
                else:
                    self.store.add_task(self.selected_day(), title.text(), notes.toPlainText(), estimate.value() * unit.currentData(), state.currentData())
                self.refresh()
            self.guard(save)

    def task_checked(self, item):
        if item.column() != 0:
            return
        identity = item.data(Qt.ItemDataRole.UserRole)
        self.ctrl.complete_task(identity, item.checkState() == Qt.CheckState.Checked)
        self.refresh_timer.start(100)

    def focus_task(self):
        identity = self.task_id()
        if identity:
            self.selected_tasks = [identity]
            if self.ctrl.timer.active:
                self.ctrl.associate([identity])
            self.refresh()
            self.task_combo.setCurrentIndex(max(0, self.task_combo.findData(identity)))
        self.navigate(1)

    def move_task(self, offset):
        tasks, row = self.store.tasks(self.selected_day()), self.tasks_table.currentRow()
        if row < 0 or not 0 <= row + offset < len(tasks):
            return
        tasks[row], tasks[row + offset] = tasks[row + offset], tasks[row]
        for position, task in enumerate(tasks):
            self.store.update("tasks", task["id"], position=position)
        self.refresh()
        self.tasks_table.selectRow(row + offset)

    def carry_task(self):
        if self.task_id():
            self.store.carry(self.task_id())
            self.refresh()

    def delete_task(self):
        if self.task_id() and QMessageBox.question(self, "删除待办", "删除这项待办？已有学习记录会保留。") == QMessageBox.StandardButton.Yes:
            self.store.update("tasks", self.task_id(), deleted=1)
            self.refresh()

    def create_focus(self):
        layout = self.page("专注时段", "选择一件事，把这一段时间留给它。")
        columns = QHBoxLayout()
        box, inner = card()
        box.setMinimumWidth(365)
        self.phase_label = label("准备开始", "subheading")
        inner.addWidget(self.phase_label)
        self.ring = Ring()
        inner.addWidget(self.ring, 1)
        self.next_label = label("", "muted")
        inner.addWidget(self.next_label)
        buttons = QHBoxLayout()
        self.start_button = button("开始专注", lambda: self.guard(self.start_focus), True)
        self.pause_button = button("暂停", lambda: self.guard(self.toggle_pause))
        self.stop_button = button("结束", self.stop_focus)
        for item in (self.start_button, self.pause_button, self.stop_button):
            buttons.addWidget(item)
        inner.addLayout(buttons)
        rests = QHBoxLayout()
        self.skip_button = button("跳过休息", self.ctrl.skip_rest)
        self.extend_button = button("休息 +5 分钟", self.ctrl.extend_rest)
        rests.addWidget(self.skip_button)
        rests.addWidget(self.extend_button)
        inner.addLayout(rests)
        note_actions = QHBoxLayout()
        note_actions.addWidget(button("随手记", self.quick_note))
        note_actions.addWidget(button("本次总结", lambda: self.show_session_summary(self.ctrl.session_id, True) if self.ctrl.session_id and not self.ctrl.timer.active else None))
        inner.addLayout(note_actions)
        columns.addWidget(box, 1)
        right = QVBoxLayout()
        box, inner = card()
        inner.addWidget(label("本次计划", "subheading"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        self.task_combo = QComboBox()
        self.task_combo.currentIndexChanged.connect(lambda _: self.select_next_task())
        self.tasks_button = button("选择关联任务（可多选）", self.choose_tasks)
        grid.addWidget(self.tasks_button, 0, 0, 1, 3)
        grid.addWidget(label("当前记录任务", "muted"), 1, 0)
        grid.addWidget(self.task_combo, 1, 1, 1, 2)
        defaults = Config(**self.store.get("timer_defaults", asdict(Config())))
        self.focus_minutes = self.duration_spin(defaults.focus / 60)
        self.short_minutes = self.duration_spin(defaults.short / 60)
        self.long_minutes = self.duration_spin(defaults.long / 60)
        self.cycles = QSpinBox()
        self.cycles.setRange(1, 99)
        self.cycles.setValue(defaults.cycles)
        self.long_every = QSpinBox()
        self.long_every.setRange(1, 99)
        self.long_every.setValue(defaults.long_every)
        self.long_enabled = QCheckBox("开启长休息")
        self.long_enabled.setChecked(defaults.long_enabled)
        self.free_mode = QCheckBox("自由计时")
        self.free_mode.setChecked(defaults.free)
        for col, (title, widget) in enumerate((("专注／分钟", self.focus_minutes), ("短休息／分钟", self.short_minutes), ("番茄数量", self.cycles))):
            grid.addWidget(label(title, "muted"), 2, col)
            grid.addWidget(widget, 3, col)
        grid.addWidget(self.long_enabled, 4, 0, 1, 2)
        grid.addWidget(self.free_mode, 4, 2)
        grid.addWidget(label("长休息间隔／个", "muted"), 5, 0)
        grid.addWidget(self.long_every, 6, 0)
        grid.addWidget(label("长休息／分钟", "muted"), 5, 1, 1, 2)
        grid.addWidget(self.long_minutes, 6, 1, 1, 2)
        self.timing_widgets = [grid.itemAt(i).widget() for i in range(grid.count()) if grid.getItemPosition(i)[0] >= 2]
        inner.addLayout(grid)
        self.estimate_label = label("", "muted")
        inner.addWidget(self.estimate_label)
        inner.addWidget(button("预约自动开始", lambda: self.reminder_form(schedule=True)))
        right.addWidget(box)
        self.config_controls = [self.focus_minutes, self.short_minutes, self.long_minutes, self.cycles, self.long_every, self.long_enabled, self.free_mode]
        for item in self.config_controls:
            if hasattr(item, "valueChanged"):
                item.valueChanged.connect(lambda *_: self.estimate_plan())
            else:
                item.toggled.connect(lambda *_: self.estimate_plan())
        box, inner = card()
        self.record_card = box
        self.note_title = label("这一段的记录", "subheading")
        inner.addWidget(self.note_title)
        self.note_content = QPlainTextEdit()
        self.note_content.setPlaceholderText("这段时间做了什么？（可选）")
        self.note_reflection = QPlainTextEdit()
        self.note_reflection.setPlaceholderText("感想、问题或收获……（可选）")
        self.note_next = QPlainTextEdit()
        self.note_next.setPlaceholderText("下一步准备做什么？（可选）")
        for item in (self.note_content, self.note_reflection, self.note_next):
            item.setMaximumHeight(55)
            item.setEnabled(False)
            item.textChanged.connect(self.queue_note)
            inner.addWidget(item)
        inner.addWidget(label("输入自动保存，填写不影响计时。", "muted"))
        right.addWidget(box)
        columns.addLayout(right, 1)
        layout.addLayout(columns)
        layout.addStretch()
        self.estimate_plan()

    @staticmethod
    def duration_spin(value):
        widget = QDoubleSpinBox()
        widget.setRange(.1, 9999)
        widget.setDecimals(1)
        widget.setValue(value)
        return widget

    def config(self):
        return Config(self.focus_minutes.value() * 60, self.short_minutes.value() * 60, self.cycles.value(),
                      self.long_enabled.isChecked(), self.long_every.value(), self.long_minutes.value() * 60, self.free_mode.isChecked())

    def estimate_plan(self):
        config = self.config()
        end = self.ctrl.clock.now() + timedelta(seconds=config.total())
        self.estimate_label.setText("自由计时，结束时记录实际投入。" if config.free else
            f"预计总计 {minutes(config.total())}，现在开始约 {end:%H:%M} 结束。\n长休息替代短休息，最后一个番茄直接完成。")
        if not self.ctrl.timer.active:
            self.long_every.setEnabled(config.long_enabled and not config.free)
            self.long_minutes.setEnabled(config.long_enabled and not config.free)
        if not self.ctrl.timer.active:
            self.update_clock()

    def select_next_task(self):
        if self.ctrl.timer.active:
            self.ctrl.switch_task(self.task_combo.currentData())
            self.statusBar().showMessage("已切换当前记录任务；原计时继续，切换前的投入保留。", 10000)

    def available_tasks(self, record=None):
        day = parse(record["start"]).astimezone().date().isoformat() if record else self.selected_day()
        tasks = self.store.tasks(day)
        extras = references(record) if record else self.store.task_references(self.selected_tasks)
        for ref in extras:
            if all(task["id"] != ref["id"] for task in tasks):
                task = self.store.one("tasks", ref["id"])
                if task:
                    tasks.append(task)
        return tasks

    def choose_tasks(self):
        form = Form("选择本次专注的任务", self)
        selected = form.task_list(self.available_tasks(), self.selected_tasks)
        form.form.addRow(label("多选后总时间只计一次。可切换当前任务留下分界，也可结束后右键划分；未分配时显示“未细分”。", "muted"))
        def save():
            self.selected_tasks = form.selected_tasks(selected)
            if self.ctrl.timer.active:
                self.ctrl.associate(self.selected_tasks)
            self.refresh()
            if not self.selected_tasks and not self.ctrl.timer.active:
                self.task_combo.setCurrentIndex(0)
        form.validate_with(save)
        form.exec()

    def start_focus(self):
        self.flush_note()
        chosen = self.selected_tasks or ([self.task_combo.currentData()] if self.task_combo.currentData() else [])
        self.ctrl.start(self.config(), self.task_combo.currentData(), task_ids=chosen)
        self.navigate(1)

    def toggle_pause(self):
        if self.ctrl.timer.active:
            self.ctrl.resume() if self.ctrl.timer.paused else self.ctrl.pause()

    def stop_focus(self):
        if self.ctrl.timer.active and QMessageBox.question(self, "结束本次计划", "结束当前计划？已投入时间和记录会保存。") == QMessageBox.StandardButton.Yes:
            self.ctrl.stop()

    def quick_note(self):
        if self.ctrl.timer.active and self.ctrl.timer.kind in ("focus", "free"):
            self.bind_note(self.ctrl.segment_id)

    def bind_note(self, identity):
        self.flush_note()
        record = self.store.one("segments", identity)
        if not record:
            return
        self.note_id = identity
        self.record_card.show()
        self.note_title.setText(self.record_title(record))
        for widget, key in ((self.note_content, "content"), (self.note_reflection, "reflection"), (self.note_next, "next_step")):
            widget.blockSignals(True)
            widget.setEnabled(True)
            widget.setPlainText(record[key])
            widget.blockSignals(False)

    def record_title(self, record):
        start = parse(record["start"]).astimezone()
        span = f"{start:%H:%M}–{parse(record['end']).astimezone():%H:%M}" if record["end"] else f"{start:%H:%M} 开始 · 进行中"
        sequence = f" · 第 {record['round']} 个番茄" if record["kind"] == "focus" else ""
        return f"{LABELS[record['kind']]}记录{sequence}｜{span}\n{task_title(record)}"

    def queue_note(self):
        if self.note_id:
            self.note_pending = (self.note_id, self.note_content.toPlainText(), self.note_reflection.toPlainText(), self.note_next.toPlainText())
            self.note_timer.start(400)

    def flush_note(self):
        self.note_timer.stop()
        if self.note_pending:
            pending, self.note_pending = self.note_pending, None
            self.ctrl.save_note(*pending)

    def phase_changed(self):
        timer = self.ctrl.timer
        active = timer.active
        for item in self.config_controls:
            item.setEnabled(not active)
        for widget in self.timing_widgets:
            widget.setVisible(not active)
        self.start_button.setEnabled(not active)
        self.pause_button.setEnabled(active)
        self.pause_button.setText("继续" if timer.paused else "暂停")
        self.stop_button.setEnabled(active)
        resting = active and timer.kind in ("short", "long")
        self.skip_button.setEnabled(resting)
        self.extend_button.setEnabled(resting)
        if active:
            title = f"{LABELS[timer.kind]} · 第 {timer.round}／{timer.config.cycles} 个"
            self.phase_label.setText(title + (" · 已暂停" if timer.paused else ""))
            if timer.kind in ("focus", "free"):
                if timer.kind == "free" or timer.round >= timer.config.cycles:
                    next_text = "这一段结束后完成本次计划"
                else:
                    is_long = timer.config.long_enabled and (timer.completed + 1) % timer.config.long_every == 0
                    next_text = f"下一个：{minutes(timer.config.long if is_long else timer.config.short)}{'长休息' if is_long else '短休息'}"
            else:
                next_text = f"下一个：第 {timer.round + 1} 个番茄"
            self.next_label.setText(next_text)
        else:
            self.phase_label.setText("本次计时已结束" if self.ctrl.session_id else "准备开始")
            self.next_label.setText("可以调整计划，开始下一次专注。")
            self.estimate_plan()
        self.update_clock()
        if (active and self.store.get("notifications", True)
                and getattr(self, "notified_segment", None) != self.ctrl.segment_id):
            self.show_notification(LABELS[timer.kind] + "开始", self.next_label.text() + ("。可以补充刚才的学习记录。" if resting else ""))
        self.notified_segment = self.ctrl.segment_id

    def show_notification(self, title, body):
        if self.notification_popup is None:
            self.notification_popup = DesktopNotice(self)
            self.notification_popup.opened.connect(self.open_notification)
        self.notification_popup.present(title, body)

    def open_notification(self):
        self.reveal()
        self.navigate(1)
        if not self.ctrl.timer.active:
            summary = next((dialog for dialog in reversed(self.dialogs) if dialog.windowTitle() == "本次专注总结"), None)
            if summary:
                summary.show()
                summary.raise_()

    def update_clock(self):
        timer = self.ctrl.timer
        if timer.active:
            seconds, free = (timer.elapsed if timer.kind == "free" else timer.remaining), timer.kind == "free"
            ratio = (timer.elapsed % 1500) / 1500 if free else timer.elapsed / timer.duration
        else:
            seconds, free, ratio = self.focus_minutes.value() * 60, False, 0
        dark = self.store.get("dark", False)
        self.ring.update_time(seconds, ratio, free, dark)
        if self.mini:
            self.mini_ring.update_time(seconds, ratio, free, dark)
            self.mini_title.setText(self.phase_label.text())
            self.mini_pause.setText("继续" if timer.paused else "暂停")
            self.mini_pause.setEnabled(timer.active)
            self.mini_stop.setEnabled(timer.active)
        if self.tray:
            self.tray.setToolTip(f"TomatoTime · {self.phase_label.text()} · {int(seconds) // 60:02}:{int(seconds) % 60:02}")

    def tick(self):
        self.ctrl.tick()
        self.update_clock()

    def show_session_summary(self, identity, manual=False):
        self.flush_note()
        session = self.store.one("sessions", identity)
        if not session or not session["end"]:
            return
        records = self.store.rows("SELECT * FROM segments WHERE session_id=?", (identity,))
        focus = sum(r["seconds"] for r in records if r["kind"] in ("focus", "free"))
        rest = sum(r["seconds"] for r in records if r["kind"] in ("short", "long"))
        count = sum(r["kind"] == "focus" and r["status"] == "complete" for r in records)
        form = Form("本次专注总结", self)
        form.form.addRow(label(f"{parse(session['start']).astimezone():%H:%M}–{parse(session['end']).astimezone():%H:%M}\n完整番茄 {count} 个 · 专注 {minutes(focus)} · 休息 {minutes(rest)}"))
        activities = form.text("这段时间做了什么", session["activities"], True)
        activities.setObjectName("sessionActivities")
        summary = form.text("总结与感想", session["summary"], True)
        summary.setObjectName("sessionSummary")
        def quote_notes():
            activities.setPlainText("\n".join(f"{parse(r['start']).astimezone():%H:%M} · {task_title(r)}：{r['content']}" for r in records if r["content"]))
        form.form.addRow(button("引用各段学习内容", quote_notes))
        form.form.addRow(label("以上两项涵盖这次启动的整个周期，独立保存；分段记录仍保留在各自时段。", "muted"))
        pending = QTimer(form)
        pending.setSingleShot(True)
        dirty = set()
        def save():
            pending.stop()
            self.flush_note()
            if "activities" in dirty:
                self.store.update("sessions", identity, activities=activities.toPlainText())
            if "summary" in dirty:
                self.store.update("sessions", identity, summary=summary.toPlainText())
            dirty.clear()
            self.refresh_timer.start(100)
        pending.timeout.connect(save)
        self.summary_savers.append(save)
        for key, item in (("summary", summary), ("activities", activities)):
            item.textChanged.connect(lambda k=key: (dirty.add(k), pending.start(400)))
        form.finished.connect(lambda *_: save())
        task_ids = list(dict.fromkeys(t["id"] for r in [session, *records] for t in references(r)))
        feedback = label("", "muted")
        feedback.setObjectName("completionFeedback")
        task_box = QWidget()
        task_rows = QVBoxLayout(task_box)
        task_rows.setContentsMargins(0, 0, 0, 0)
        states = []
        for task_id in task_ids:
            task = self.store.one("tasks", task_id)
            if not task:
                continue
            row = QHBoxLayout()
            complete = QPushButton()
            undo = QPushButton("撤销")
            complete.setToolTip(task["title"])
            complete.clicked.connect(lambda checked=False, i=task_id: feedback.setText(self.ctrl.complete_task(i)))
            undo.clicked.connect(lambda checked=False, i=task_id: feedback.setText(self.ctrl.complete_task(i, False)))
            row.addWidget(complete, 1)
            row.addWidget(undo)
            task_rows.addLayout(row)
            states.append((task_id, complete, undo))
        def refresh_states():
            for i, complete, undo in states:
                task = self.store.one("tasks", i)
                done = task and task["status"] == "done"
                complete.setText(("✓ 已完成 · " if done else "标记完成 · ") + (task["title"][:25] if task else "任务不存在"))
                complete.setEnabled(bool(task) and not done)
                undo.setEnabled(bool(done))
        refresh_states()
        self.ctrl.changed.connect(refresh_states)
        if states:
            scroller = QScrollArea()
            scroller.setWidgetResizable(True)
            scroller.setWidget(task_box)
            scroller.setFixedHeight(min(150, len(states) * 45 + 15))
            form.form.addRow("任务完成情况", scroller)
            form.form.addRow(feedback)
        form.buttons.button(form.buttons.StandardButton.Ok).setText("保存并关闭")
        form.buttons.button(form.buttons.StandardButton.Cancel).hide()
        self.dialogs.append(form)
        if self.isVisible() or manual or (self.mini and self.mini.isVisible()):
            form.show()
        if self.store.get("notifications", True) and not manual:
            self.show_notification("本次计划结束", f"专注 {minutes(focus)}，可以写下本次收获。")

    def flush_summaries(self):
        for save in self.summary_savers:
            save()

    def make_timeline(self):
        tree = QTreeWidget()
        tree.setHeaderLabels(("时间", "阶段", "任务／记录", "时长与状态"))
        tree.setMinimumHeight(225)
        tree.setWordWrap(True)
        tree.setColumnWidth(0, 220)
        tree.setColumnWidth(1, 85)
        tree.setColumnWidth(2, 300)
        tree.itemDoubleClicked.connect(lambda item, _: self.edit_record(item))
        tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        tree.customContextMenuRequested.connect(lambda point, t=tree: self.timeline_menu(t, point))
        return tree

    def timeline_menu(self, tree, point):
        item = tree.itemAt(point)
        if not item:
            return
        tree.setCurrentItem(item)
        identity = item.data(0, Qt.ItemDataRole.UserRole)
        record = self.store.one("segments", identity)
        menu = QMenu(tree)
        menu.addAction("编辑内容与感想", lambda: self.edit_record(identity))
        menu.addAction("增加／移除关联任务", lambda: self.association_form(identity))
        split = menu.addAction("按时间划分任务", lambda: self.split_form(identity))
        time = menu.addAction("修正时间与非专注部分", lambda: self.manual_form(record))
        undo = menu.addAction("撤销上次调整", lambda: self.guard(lambda: (self.store.undo_segment(identity), self.refresh())))
        split.setEnabled(bool(record["end"]) and record["kind"] in ("focus", "free"))
        time.setEnabled(bool(record["end"]))
        undo.setEnabled(bool(record["end"]) and bool(json.loads(record["revisions"])))
        menu.addAction("查看原始记录", lambda: self.original_form(identity))
        menu.exec(tree.viewport().mapToGlobal(point))

    def original_form(self, identity):
        record = self.store.one("segments", identity)
        form = Form("原始记录与调整历史", self)
        history = json.loads(record["revisions"])
        original = json.loads(record["original"]) if record["original"] else record
        text = form.text("原始计时", json.dumps(original, ensure_ascii=False, indent=2), True)
        text.setMaximumHeight(360)
        text.setReadOnly(True)
        form.form.addRow(label("\n".join(f"{parse(row['time']).astimezone():%m-%d %H:%M} · {row['reason']}" for row in history) or "尚未调整"))
        form.buttons.button(form.buttons.StandardButton.Ok).setText("关闭")
        form.buttons.button(form.buttons.StandardButton.Cancel).hide()
        form.exec()

    def association_form(self, identity):
        record = self.store.one("segments", identity)
        form = Form("调整这个时间段的关联任务", self)
        selected = form.task_list(self.available_tasks(record), [t["id"] for t in references(record)])
        form.form.addRow(label("仅调整归属，不增加计时。多任务可稍后按时间划分，未明确分配的部分单独显示。", "muted"))
        def save():
            ids = form.selected_tasks(selected)
            if identity == self.ctrl.segment_id and self.ctrl.timer.active:
                self.selected_tasks = ids
                self.ctrl.associate(ids)
            else:
                self.store.associate_segment(identity, ids)
            self.refresh()
        form.validate_with(save)
        form.exec()

    def split_form(self, identity):
        record = self.store.one("segments", identity)
        if not record["end"]:
            return
        form = Form("按时间划分任务（原计时只计一次）", self)
        form.resize(760, 480)
        start, finish = parse(record["start"]), parse(record["end"])
        form.form.addRow(label(f"原时段 {start.astimezone():%Y-%m-%d %H:%M:%S} — {finish.astimezone():%H:%M:%S}\n有效专注 {minutes(record['seconds'])}；暂停会自动扣除。请确认分界时间后保存。", "muted"))
        allocation_table = table(("开始", "结束／分界", "任务", "有效投入"))
        allocation_table.setMinimumHeight(190)
        form.form.addRow(allocation_table)
        tasks = self.available_tasks(record)
        entries = [("未细分任务", None)] + [(t["title"], t["id"]) for t in tasks]
        boundaries = []
        def refresh_amounts(*_):
            cursor = start
            for row, (boundary, task) in enumerate(boundaries):
                edge = boundary.dateTime().toPython().astimezone()
                allocation_table.setItem(row, 0, QTableWidgetItem(f"{cursor.astimezone():%H:%M:%S}"))
                amount = max(0, effective_before(json.loads(record["intervals"]), edge) - effective_before(json.loads(record["intervals"]), cursor))
                allocation_table.setItem(row, 3, QTableWidgetItem(minutes(amount)))
                cursor = edge
        def add_row(edge, task_id):
            row = len(boundaries)
            allocation_table.setRowCount(row + 1)
            from PySide6.QtWidgets import QDateTimeEdit
            boundary = QDateTimeEdit(edge)
            boundary.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
            task = QComboBox()
            for title, value in entries:
                task.addItem(title, value)
            task.setCurrentIndex(max(0, task.findData(task_id)))
            allocation_table.setCellWidget(row, 1, boundary)
            allocation_table.setCellWidget(row, 2, task)
            boundaries.append((boundary, task))
            boundary.dateTimeChanged.connect(refresh_amounts)
        configured = assignments(record)
        if record["allocation_mode"] != "auto" and len(configured) > 1:
            for i, a in enumerate(configured):
                edge = parse(a["intervals"][-1]["end"]) if a["intervals"] else start
                add_row(finish if i == len(configured) - 1 else edge, a["task_id"])
        elif len(references(record)) >= 2:
            add_row(start + (finish - start) / 2, references(record)[0]["id"])
            add_row(finish, references(record)[1]["id"])
        else:
            add_row(finish, references(record)[0]["id"] if references(record) else None)
        boundaries[-1][0].setReadOnly(True)
        refresh_amounts()
        def add_boundary():
            last_time, last_task = boundaries[-1]
            preceding = start if len(boundaries) == 1 else boundaries[-2][0].dateTime().toPython().astimezone()
            last_time.setReadOnly(False)
            last_time.setDateTime(preceding + (finish - preceding) / 2)
            add_row(finish, None)
            boundaries[-1][0].setReadOnly(True)
            refresh_amounts()
        def remove_boundary():
            if len(boundaries) <= 1:
                return
            row = allocation_table.currentRow()
            row = max(0, min(row, len(boundaries) - 2))
            boundaries.pop(row)
            allocation_table.removeRow(row)
            boundaries[-1][0].setDateTime(finish)
            boundaries[-1][0].setReadOnly(True)
            refresh_amounts()
        actions = QHBoxLayout()
        actions.addWidget(button("增加分界", add_boundary))
        actions.addWidget(button("删除选中分界", remove_boundary))
        form.form.addRow(actions)
        estimated = any(abs((parse(f["end"]) - parse(f["start"])).total_seconds() - f["seconds"]) > .1 for f in json.loads(record["intervals"]))
        estimate = form.check("这条补记没有休息位置，同意按区间比例估算任务投入", False) if estimated else None
        def save():
            ranges = [(finish if i == len(boundaries) - 1 else boundary.dateTime().toPython().astimezone(), task.currentData())
                      for i, (boundary, task) in enumerate(boundaries)]
            self.store.split_segment(identity, ranges, bool(estimate and estimate.isChecked()))
            self.refresh()
        form.validate_with(save)
        form.exec()

    def fill_timeline(self, tree, records, day=None):
        tree.clear()
        status = {"complete": "完成", "running": "进行中", "paused": "暂停", "stopped": "提前结束", "skipped": "跳过", "manual": "补记", "unconfirmed": "未确认"}
        for r in records:
            start = parse(r["start"]).astimezone()
            end = parse(r["end"]).astimezone() if r["end"] else None
            display = f"{start:%H:%M}–{end:%H:%M}" if end else f"{start:%H:%M}–进行中"
            if not day or (end and start.date() != end.date()):
                display = f"{start:%m-%d} " + display
            seconds = day_seconds(json.loads(r["intervals"])).get(day, 0) if day else r["seconds"]
            item = QTreeWidgetItem((display, LABELS[r["kind"]], task_title(r), f"{minutes(seconds)} · {status.get(r['status'], r['status'])}"))
            item.setData(0, Qt.ItemDataRole.UserRole, r["id"])
            if end:
                span_seconds = (end - start).total_seconds()
                item.setToolTip(3, f"起止跨度 {minutes(span_seconds)}；有效投入 {minutes(r['seconds'])}；未计入 {minutes(max(0, span_seconds-r['seconds']))}。")
            tree.addTopLevelItem(item)
            if r["kind"] in ("focus", "free") and (len(references(r)) > 1 or r["allocation_mode"] != "auto"):
                for allocation in assignments(r):
                    fragments = allocation["intervals"]
                    amount = day_seconds(fragments).get(day, 0) if day else allocation["seconds"]
                    span = f"{parse(fragments[0]['start']).astimezone():%H:%M:%S}–{parse(fragments[-1]['end']).astimezone():%H:%M:%S}" if fragments else "待计时"
                    child = QTreeWidgetItem((span, "任务划分", allocation["task_name"], minutes(amount)))
                    child.setData(0, Qt.ItemDataRole.UserRole, r["id"])
                    item.addChild(child)
            for title, key in (("内容", "content"), ("感想", "reflection"), ("下一步", "next_step")):
                if r[key]:
                    child = QTreeWidgetItem(("", title, r[key], ""))
                    child.setData(0, Qt.ItemDataRole.UserRole, r["id"])
                    child.setToolTip(2, r[key])
                    item.addChild(child)
            item.setExpanded(True)

    def edit_record(self, item):
        identity = item if isinstance(item, int) else item.data(0, Qt.ItemDataRole.UserRole)
        record = self.store.one("segments", identity)
        form = Form("编辑时间段记录", self)
        form.form.addRow(label(f"{parse(record['start']).astimezone():%Y-%m-%d %H:%M} · {LABELS[record['kind']]}"))
        content = form.text("做了什么", record["content"], True)
        reflection = form.text("感想／问题", record["reflection"], True)
        next_step = form.text("下一步", record["next_step"], True)
        tasks = [("未关联任务", None)] + [(t["title"], t["id"]) for t in self.store.tasks(parse(record["start"]).astimezone().date().isoformat())]
        if record["task_id"] and all(t[1] != record["task_id"] for t in tasks):
            tasks.append((record["task_name"] + "（历史任务）", record["task_id"]))
        selected = form.task_list(self.available_tasks(record), [t["id"] for t in references(record)])
        def save():
            self.ctrl.save_note(identity, content.toPlainText(), reflection.toPlainText(), next_step.toPlainText())
            ids = form.selected_tasks(selected)
            if ids != [t["id"] for t in references(record)]:
                if identity == self.ctrl.segment_id and self.ctrl.timer.active:
                    self.selected_tasks = ids
                    self.ctrl.associate(ids)
                else:
                    self.store.associate_segment(identity, ids)
            if identity == self.note_id:
                self.bind_note(identity)
            self.refresh()
        form.validate_with(save)
        form.exec()

    def manual_form(self, record=None):
        form = Form("修正时间（保留原始信息）" if record else "补记时间段", self)
        date = self.day_picker.date().toPython()
        initial_end = now().replace(year=date.year, month=date.month, day=date.day, second=0, microsecond=0)
        start = form.date_time("开始时间", parse(record["start"]) if record else initial_end - timedelta(hours=1), seconds=True)
        end = form.date_time("结束时间", parse(record["end"]) if record else initial_end, seconds=True)
        start.setObjectName("manualStart")
        end.setObjectName("manualEnd")
        original_start_widget, original_end_widget = start.dateTime().toPython(), end.dateTime().toPython()
        span = (parse(record["end"]) - parse(record["start"])).total_seconds() if record else 3600
        has_break = form.check("期间包含休息／离开", bool(record and span - record["seconds"] > .5))
        excluded = form.number("其中非专注／分钟", max(0, (span - record["seconds"]) / 60) if record else 0, decimal=True)
        excluded.setDecimals(2)
        only_record = form.check("只保存记录，不累计专注时长", bool(record and record["status"] == "unconfirmed"))
        calculated = label("", "muted")
        calculated.setObjectName("manualDuration")
        form.form.addRow(calculated)
        selected = form.task_list(self.available_tasks(record), [t["id"] for t in references(record)] if record else [])
        content = form.text("做了什么", record["content"] if record else "", True)
        content.setObjectName("manualContent")
        reflection = form.text("感想／问题", record["reflection"] if record else "", True)
        alignment = {}
        def dates():
            first, last = start.dateTime().toPython().astimezone(), end.dateTime().toPython().astimezone()
            if record and start.dateTime().toPython() == original_start_widget:
                first = parse(record["start"])
            if record and end.dateTime().toPython() == original_end_widget:
                last = parse(record["end"])
            if alignment and start.dateTime().toPython() == alignment["widget"]:
                first = alignment["exact"]
            return first, last
        def update_duration(*_):
            first, last = dates()
            total = (last - first).total_seconds()
            actual = max(0, total - (excluded.value() * 60 if has_break.isChecked() else 0))
            excluded.setVisible(has_break.isChecked())
            form.form.labelForField(excluded).setVisible(has_break.isChecked())
            calculated.setText(f"时段共 {minutes(max(0, total))}；" + ("仅保存记录，不计时" if only_record.isChecked() else f"计入专注 {minutes(actual)}"))
        for widget in (start, end):
            widget.dateTimeChanged.connect(update_duration)
        has_break.toggled.connect(update_duration)
        only_record.toggled.connect(update_duration)
        excluded.valueChanged.connect(update_duration)
        update_duration()
        align_button = button("紧接冲突记录之后开始", lambda: None)
        align_button.setEnabled(False)
        def align():
            boundary = form.validation_exception.conflict_end
            start.setDateTime(boundary)
            alignment.update(exact=boundary, widget=start.dateTime().toPython())
            update_duration()
        align_button.clicked.disconnect()
        align_button.clicked.connect(align)
        form.validation_failed.connect(lambda error: align_button.setEnabled(hasattr(error, "conflict_end")))
        form.form.addRow(align_button)
        if record:
            form.form.addRow(label("只改文字会保留原有效时间片；修改时间或扣除量会重算并清除旧任务划分，原信息可右键查看或撤销。", "muted"))
        def save():
            first, last = dates()
            actual = None if only_record.isChecked() else (last - first).total_seconds() - (excluded.value() * 60 if has_break.isChecked() else 0)
            ids = form.selected_tasks(selected)
            unchanged = (record and first == parse(record["start"]) and last == parse(record["end"]) and
                         ((actual is None and record["status"] == "unconfirmed") or (actual is not None and abs(actual - record["seconds"]) < .5)))
            if unchanged:
                self.store.revise_segment(record["id"], "补充记录内容", content=content.toPlainText(), reflection=reflection.toPlainText())
                if ids != [t["id"] for t in references(record)]:
                    self.store.associate_segment(record["id"], ids)
            else:
                self.store.manual(first, last, actual, ids[0] if len(ids) == 1 else None, content.toPlainText(), reflection.toPlainText(),
                                  record["id"] if record else None, task_ids=ids)
            self.refresh()
        form.validate_with(save)
        form.exec()

    def create_history(self):
        layout = self.page("学习日志", "时间、内容和感想，一起留下。双击时间段可以补充记录。")
        actions = QHBoxLayout()
        self.history_date = QDateEdit(QDate.currentDate())
        self.history_date.setCalendarPopup(True)
        self.history_date.setDisplayFormat("yyyy-MM-dd")
        self.history_date.dateChanged.connect(self.refresh_history)
        self.history_search = QLineEdit()
        self.history_search.setPlaceholderText("搜索全部历史：任务、内容、感想……")
        self.history_search.textChanged.connect(self.refresh_history)
        actions.addWidget(self.history_date)
        actions.addWidget(self.history_search, 1)
        actions.addWidget(button("导出当天", lambda: self.export_day(self.history_date.date().toString("yyyy-MM-dd"))))
        actions.addWidget(button("导出日期范围", self.export_range))
        actions.addWidget(button("周期总结", self.browse_summaries))
        layout.addLayout(actions)
        self.history_tree = self.make_timeline()
        self.history_tree.setMinimumHeight(380)
        layout.addWidget(self.history_tree)
        self.history_summary = label("", "muted")
        layout.addWidget(self.history_summary)
        self.history_review = QPlainTextEdit()
        self.history_review.setReadOnly(True)
        self.history_review.setMaximumHeight(160)
        layout.addWidget(self.history_review)
        layout.addStretch()

    def browse_summaries(self):
        day = self.history_date.date().toString("yyyy-MM-dd")
        sessions = [s for s in self.store.rows("SELECT * FROM sessions WHERE end IS NOT NULL ORDER BY start") if parse(s["start"]).astimezone().date().isoformat() == day]
        if not sessions:
            QMessageBox.information(self, "周期总结", "该日尚无已结束的专注计划。")
            return
        form = Form("选择专注计划", self)
        selected = form.choice("本次计划", [(f"{parse(s['start']).astimezone():%H:%M}–{parse(s['end']).astimezone():%H:%M} · {s['task_name'] or '未关联任务'}", s["id"]) for s in sessions])
        if form.exec():
            self.show_session_summary(selected.currentData(), True)

    def refresh_history(self, *_):
        if not hasattr(self, "history_tree"):
            return
        day = self.history_date.date().toString("yyyy-MM-dd")
        text = self.history_search.text().strip().casefold()
        records = self.store.rows("SELECT * FROM segments ORDER BY start,id") if text else self.store.segments(day)
        if text:
            records = [r for r in records if text in " ".join(r[k] or "" for k in ("task_name", "content", "reflection", "next_step")).casefold()]
        self.fill_timeline(self.history_tree, records, None if text else day)
        stats = self.store.stats(day)
        self.history_summary.setText(f"{day} · 专注 {minutes(stats['focus'])} · 完整番茄 {stats['completed']} 个")
        self.history_review.setPlainText(self.store.day(day)["reflection"])

    def close_day(self):
        if self.ctrl.timer.active:
            QMessageBox.information(self, "仍在专注", "当前计划尚未结束，请先结束计划后再结束今天。")
            return
        self.flush_day()
        self.flush_note()
        day = self.selected_day()
        self.store.save_day(day, closed=1)
        folder = self.store.get("archive_folder")
        if folder:
            self.guard(lambda: self.archive_day(day, Path(folder)))
        self.refresh()
        self.statusBar().showMessage("当天总结已保存，之后仍可补记或继续学习。AI 点评为后续可选功能。", 15000)

    def archive_day(self, day, folder):
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{day}.md"
        if path.exists():
            path = folder / f"{day}-{now():%H%M%S-%f}.md"
        path.write_text(self.store.markdown(day), encoding="utf-8")

    def export_day(self, day):
        self.flush_note()
        self.flush_day()
        path, _ = QFileDialog.getSaveFileName(self, "导出学习日志", f"{day}.md", "Markdown (*.md)")
        if path:
            self.guard(lambda: Path(path).write_text(self.store.markdown(day), encoding="utf-8"))

    def export_range(self):
        form = Form("导出日期范围", self)
        start = form.date_time("从", now() - timedelta(days=7))
        end = form.date_time("到", now())
        if form.exec():
            first, last = start.dateTime().toPython().date(), end.dateTime().toPython().date()
            if last < first or (last - first).days > 3660:
                QMessageBox.warning(self, "日期范围", "请选择有效范围，最长十年。")
                return
            folder = QFileDialog.getExistingDirectory(self, "选择导出目录")
            if folder:
                self.flush_day()
                self.flush_note()
                for offset in range((last - first).days + 1):
                    self.guard(lambda d=(first + timedelta(days=offset)).isoformat(): self.archive_day(d, Path(folder)))
                self.statusBar().showMessage("日期范围日志已导出；已有同名文件时生成新版本。", 10000)

    def reminder_form(self, schedule=False, existing=None):
        form = Form("预约自动开始" if schedule else "事项提醒", self)
        title = form.text("标题", existing["title"] if existing else "自动开始专注" if schedule else "")
        due = form.date_time("日期与时间", parse(existing["due"]) if existing else None)
        note = form.text("备注", existing["note"] if existing else "", True)
        task = form.choice("关联待办", [("未关联任务", None)] + [(t["title"], t["id"]) for t in self.store.tasks(self.selected_day())],
                           existing["task_id"] if existing else self.task_combo.currentData() if schedule else None)
        repeat = form.choice("重复", [("单次", "once"), ("每天", "daily"), ("工作日", "weekdays"), ("每周指定星期", "weekly")], existing["repeat"] if existing else "once")
        checks = form.weekdays(json.loads(existing["weekdays"]) if existing else [])
        sounds = [("使用默认提醒音", "default"), ("静音", "silent")] + [(p.name, p.name) for p in self.store.audio_dir.iterdir() if p.is_file() and not p.name.startswith("builtin-")]
        sound = form.choice("音效", sounds, existing["sound"] if existing else "default")
        if schedule:
            form.form.addRow(label("使用当前专注参数；到点已有计划时跳过。\n应用需保持运行，收至托盘也有效。", "muted"))
        if form.exec():
            def save():
                config = json.loads(existing["config"]) if existing and schedule else asdict(self.config()) if schedule else {}
                if schedule and not existing:
                    config["_task_ids"] = list(self.selected_tasks)
                self.ctrl.add_reminder(title.text(), due.dateTime().toPython().astimezone(), note.toPlainText(), task.currentData(),
                    repeat.currentData(), [i for i, check in enumerate(checks) if check.isChecked()],
                    "schedule" if schedule else "reminder", config, sound.currentData(), existing["id"] if existing else None)
                self.refresh()
            self.guard(save)

    def manage_reminders(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("提醒与预约")
        dialog.resize(850, 570)
        layout = QVBoxLayout(dialog)
        items = table(("标题", "时间", "类型", "重复", "状态"))
        layout.addWidget(items)
        history = table(("最近提醒", "计划时间", "处理情况"))
        history.setMaximumHeight(170)
        layout.addWidget(history)
        def refresh():
            records = self.store.rows("SELECT * FROM reminders WHERE kind!='deleted' ORDER BY due")
            items.setRowCount(len(records))
            for row, r in enumerate(records):
                for col, text in enumerate((r["title"], f"{parse(r['due']).astimezone():%Y-%m-%d %H:%M}", "预约" if r["kind"] == "schedule" else "提醒", r["repeat"], "启用" if r["enabled"] else "暂停／已触发")):
                    item = QTableWidgetItem(text)
                    item.setData(Qt.ItemDataRole.UserRole, r["id"])
                    items.setItem(row, col, item)
            occurrences = self.store.rows("SELECT * FROM occurrences ORDER BY fired DESC LIMIT 20")
            history.setRowCount(len(occurrences))
            statuses = {"pending": "待处理", "done": "已处理", "seen": "已查看", "snoozed": "稍后提醒", "skipped": "已跳过", "started": "已启动", "canceled": "已取消"}
            for row, r in enumerate(occurrences):
                for col, text in enumerate((r["title"], f"{parse(r['planned']).astimezone():%m-%d %H:%M}", statuses.get(r["status"], r["status"]))):
                    history.setItem(row, col, QTableWidgetItem(text))
        def selected():
            row = items.currentRow()
            return self.store.one("reminders", items.item(row, 0).data(Qt.ItemDataRole.UserRole)) if row >= 0 else None
        def edit():
            r = selected()
            if r:
                self.reminder_form(r["kind"] == "schedule", r)
                refresh()
        def toggle():
            r = selected()
            if r:
                if not r["enabled"] and parse(r["due"]) <= self.ctrl.clock.now():
                    edit()
                    return
                self.store.update("reminders", r["id"], enabled=int(not r["enabled"]))
                refresh()
                self.refresh()
        def delete():
            r = selected()
            if r:
                self.store.update("reminders", r["id"], enabled=0, kind="deleted")
                for occurrence in self.store.rows("SELECT id FROM occurrences WHERE reminder_id=? AND status IN ('pending','snoozed')", (r["id"],)):
                    self.ctrl.acknowledge([occurrence["id"]], "canceled")
                refresh()
                self.refresh()
        actions = QHBoxLayout()
        for text, fn in (("新增", lambda: (self.reminder_form(), refresh())), ("编辑", edit), ("启用／暂停", toggle), ("删除", delete)):
            actions.addWidget(button(text, fn))
        layout.addLayout(actions)
        items.cellDoubleClicked.connect(lambda *_: edit())
        refresh()
        self.dialogs.append(dialog)
        dialog.show()

    def show_notices(self, records):
        self.notice_records.extend(records)
        if not self.notice_dialog:
            dialog = QDialog(self, Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
            dialog.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
            dialog.setWindowTitle("TomatoTime · 到时间了")
            dialog.resize(500, 380)
            layout = QVBoxLayout(dialog)
            layout.addWidget(label("到时间了", "heading"))
            self.notice_text = QPlainTextEdit()
            self.notice_text.setReadOnly(True)
            layout.addWidget(self.notice_text)
            actions = QHBoxLayout()
            actions.addWidget(button("已处理", lambda: self.finish_notices("done"), True))
            actions.addWidget(button("关闭", lambda: self.finish_notices("seen")))
            layout.addLayout(actions)
            snooze = QHBoxLayout()
            self.snooze_minutes = QSpinBox()
            self.snooze_minutes.setRange(1, 1440)
            self.snooze_minutes.setValue(10)
            snooze.addWidget(label("稍后／分钟"))
            snooze.addWidget(self.snooze_minutes)
            for value in (5, 10, 30):
                snooze.addWidget(button(str(value), lambda checked=False, v=value: self.snooze_minutes.setValue(v)))
            snooze.addWidget(button("稍后提醒", lambda: self.finish_notices("snoozed")))
            layout.addLayout(snooze)
            layout.addWidget(button("打开关联待办", self.open_notice_task))
            dialog.finished.connect(self.notice_closed)
            self.notice_dialog = dialog
        self.notice_text.setPlainText("\n\n".join(("错过的提醒 · " if r["missed"] else "") + f"{parse(r['planned']).astimezone():%m-%d %H:%M}\n{r['title']}\n{r['note']}" for r in self.notice_records))
        self.notice_dialog.show()

    def finish_notices(self, status):
        self.ctrl.acknowledge([r["id"] for r in self.notice_records], status, self.snooze_minutes.value())
        self.notice_records = []
        self.notice_dialog.close()

    def notice_closed(self, *_):
        if self.notice_records:
            self.ctrl.acknowledge([r["id"] for r in self.notice_records], "seen")
        self.notice_records = []
        self.notice_dialog = None

    def open_notice_task(self):
        task = next((self.store.one("tasks", r["task_id"]) for r in self.notice_records if r["task_id"]), None)
        self.reveal()
        self.navigate(0)
        if task:
            self.day_picker.setDate(QDate.fromString(task["day"], "yyyy-MM-dd"))
            for row in range(self.tasks_table.rowCount()):
                if self.tasks_table.item(row, 0).data(Qt.ItemDataRole.UserRole) == task["id"]:
                    self.tasks_table.selectRow(row)

    def create_settings(self):
        layout = self.page("设置", "让它适合你的专注习惯。")
        box, inner = card()
        inner.addWidget(label("外观与窗口", "subheading"))
        self.dark_check = QCheckBox("深色模式")
        self.dark_check.setChecked(self.store.get("dark", False))
        self.dark_check.toggled.connect(lambda value: (self.store.set("dark", value), self.apply_theme()))
        inner.addWidget(self.dark_check)
        self.close_to_tray = QCheckBox("关闭主窗口时收至托盘，继续计时和提醒")
        self.close_to_tray.setChecked(self.store.get("close_to_tray", True))
        self.close_to_tray.toggled.connect(lambda value: self.store.set("close_to_tray", value))
        inner.addWidget(self.close_to_tray)
        notifications = QCheckBox("阶段切换和周期结束时显示桌面小弹窗")
        notifications.setChecked(self.store.get("notifications", True))
        notifications.toggled.connect(lambda value: self.store.set("notifications", value))
        inner.addWidget(notifications)
        inner.addWidget(label("应用退出或电脑睡眠时不保证准时提醒，重新打开后会提示错过事项。", "muted"))
        layout.addWidget(box)
        box, inner = card()
        inner.addWidget(label("音效", "subheading"))
        controls = QHBoxLayout()
        muted = QCheckBox("全部静音")
        muted.setChecked(self.store.get("muted", False))
        muted.toggled.connect(lambda value: self.store.set("muted", value))
        controls.addWidget(muted)
        volume = QSpinBox()
        volume.setRange(0, 100)
        volume.setSuffix(" %")
        volume.setValue(self.store.get("volume", 60))
        volume.valueChanged.connect(lambda value: self.store.set("volume", value))
        controls.addWidget(label("音量"))
        controls.addWidget(volume)
        controls.addStretch()
        inner.addLayout(controls)
        inner.addWidget(label("桌面小弹窗不触发 Windows 提示音，提醒声音统一由这里控制。", "muted"))
        self.sound_combos = {}
        for event, title in EVENTS.items():
            row = QHBoxLayout()
            row.addWidget(label(title))
            combo = QComboBox()
            combo.setMinimumWidth(250)
            combo.addItem("默认 · " + PRESETS[DEFAULT_PRESETS[event]][0], "default")
            for preset, (name, _) in PRESETS.items():
                combo.addItem(name, "preset:" + preset)
            combo.addItem("静音", "silent")
            value = self.store.get("sound:" + event, "default")
            if combo.findData(value) < 0:
                combo.addItem("自定义 · " + value, value)
            combo.setCurrentIndex(max(0, combo.findData(value)))
            combo.currentIndexChanged.connect(lambda _, e=event, c=combo: self.store.set("sound:" + e, c.currentData()))
            self.sound_combos[event] = combo
            row.addWidget(combo, 1)
            row.addWidget(button("选择文件", lambda checked=False, e=event: self.import_sound(e)))
            row.addWidget(button("试听", lambda checked=False, e=event: self.audio.play(e)))
            inner.addLayout(row)
        def restore_sounds():
            muted.setChecked(False)
            volume.setValue(60)
            self.store.set("muted", False)
            self.store.set("volume", 60)
            for event, combo in self.sound_combos.items():
                combo.setCurrentIndex(combo.findData("default"))
                self.store.set("sound:" + event, "default")
            self.statusBar().showMessage("已恢复全部内置提示音，音量为 60%。可以点击各项“试听”检查声音。", 15000)
        restore = button("恢复内置音效", restore_sounds)
        restore.setObjectName("restoreBuiltinSounds")
        inner.addWidget(restore)
        layout.addWidget(box)
        box, inner = card()
        inner.addWidget(label("日志与备份", "subheading"))
        self.data_path = label(str(self.store.directory), "muted")
        inner.addWidget(self.data_path)
        actions = QHBoxLayout()
        actions.addWidget(button("导出完整备份", self.backup))
        actions.addWidget(button("恢复备份", self.restore))
        actions.addWidget(button("设置每日归档目录", self.choose_archive))
        actions.addWidget(button("关闭自动归档", lambda: self.store.set("archive_folder", None)))
        inner.addLayout(actions)
        inner.addWidget(label("启用后，点击“结束今天”保存 Markdown；同名日志存在时另存一个版本。", "muted"))
        layout.addWidget(box)
        box, inner = card()
        inner.addWidget(label("AI 与 DSH（可选，后续实现）", "subheading"))
        inner.addWidget(label("当前版本不向外部服务发送学习记录。API 点评和 deepseek-harness 接入将在本地功能稳定后单独实现。", "muted"))
        layout.addWidget(box)
        layout.addStretch()

    def import_sound(self, event):
        path, _ = QFileDialog.getOpenFileName(self, "选择提示音", "", "音频 (*.mp3 *.wav *.ogg)")
        if path:
            def save():
                name = self.audio.import_file(path)
                combo = self.sound_combos[event]
                if combo.findData(name) < 0:
                    combo.addItem("自定义 · " + Path(path).name, name)
                combo.setCurrentIndex(combo.findData(name))
            self.guard(save)

    def backup(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出完整备份", f"tomatotime-{now():%Y%m%d}.json", "JSON (*.json)")
        if path:
            self.flush_day()
            self.flush_note()
            self.flush_summaries()
            self.ctrl.save()
            self.guard(lambda: self.store.backup(Path(path)))

    def restore(self):
        if self.ctrl.timer.active:
            QMessageBox.information(self, "请先结束计划", "恢复备份前请结束当前专注计划。")
            return
        path, _ = QFileDialog.getOpenFileName(self, "恢复备份", "", "JSON (*.json)")
        if not path:
            return
        if QMessageBox.question(self, "恢复备份", "替换当前数据？恢复前会自动保存一份现有数据备份。恢复后请重新启动应用。") != QMessageBox.StandardButton.Yes:
            return
        def perform():
            self.flush_day()
            self.flush_note()
            self.flush_summaries()
            self.audio.close()
            QApplication.processEvents()
            self.store.restore(Path(path))
            # 避免退出过程中的旧状态覆盖恢复出来的数据。
            self.ctrl.timer.active = False
            self.ctrl.session_id = self.ctrl.segment_id = None
            self.note_pending = self.day_pending = None
            self.summary_savers.clear()
            self.tick_timer.stop()
            self.exit_app(save_state=False)
        self.guard(perform)

    def choose_archive(self):
        folder = QFileDialog.getExistingDirectory(self, "每日 Markdown 归档目录")
        if folder:
            self.store.set("archive_folder", folder)
            self.statusBar().showMessage("每日归档已启用：" + folder, 15000)

    def apply_theme(self):
        self.setStyleSheet(theme(self.store.get("dark", False)))
        if self.mini:
            self.mini.setStyleSheet(self.styleSheet())
        self.update_clock()

    def set_pinned(self, value):
        self.store.set("pinned", value)
        visible = self.isVisible()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, value)
        if visible:
            self.show()
        # 改变顶层窗口标志不应让页面跳到刚才获得焦点的底部控件。
        self.pages.currentWidget().verticalScrollBar().setValue(0)

    def show_mini(self):
        if not self.mini:
            self.mini = QWidget(None, Qt.WindowType.Window)
            self.mini.setWindowTitle("TomatoTime · 迷你")
            self.mini.setWindowIcon(icon())
            self.mini.resize(290, 330)
            layout = QVBoxLayout(self.mini)
            self.mini_title = label("专注", "muted")
            layout.addWidget(self.mini_title)
            self.mini_ring = Ring(True)
            layout.addWidget(self.mini_ring, 1)
            controls = QHBoxLayout()
            self.mini_pause = button("暂停", self.toggle_pause)
            controls.addWidget(self.mini_pause)
            controls.addWidget(button("记录", self.quick_record_popup))
            self.mini_stop = button("结束本次", self.stop_focus)
            controls.addWidget(self.mini_stop)
            layout.addLayout(controls)
            pin = QCheckBox("始终置顶")
            pin.setChecked(self.store.get("mini_pinned", True))
            def set_pin(value):
                self.store.set("mini_pinned", value)
                self.mini.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, value)
                self.mini.show()
            pin.toggled.connect(set_pin)
            layout.addWidget(pin)
            self.mini.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, pin.isChecked())
            geometry = self.store.get("mini_geometry")
            if geometry:
                self.mini.restoreGeometry(bytes.fromhex(geometry))
            self.mini.setStyleSheet(self.styleSheet())
        self.update_clock()
        self.mini.show()

    def quick_record_popup(self):
        if not self.ctrl.timer.active:
            if self.ctrl.session_id:
                self.show_session_summary(self.ctrl.session_id, True)
            return
        identity = self.ctrl.segment_id if self.ctrl.timer.kind in ("focus", "free") else self.ctrl.last_note
        if not identity:
            return
        self.flush_note()
        record = self.store.one("segments", identity)
        form = Form("记录当前专注" if self.ctrl.timer.kind in ("focus", "free") else "记录刚结束的专注", self.mini)
        form.form.addRow(label(self.record_title(record), "muted"))
        content = form.text("做了什么", record["content"], True)
        reflection = form.text("感想／问题", record["reflection"], True)
        next_step = form.text("下一步（可选）", record["next_step"], True)
        dirty = set()
        pending = QTimer(form)
        pending.setSingleShot(True)
        fields = {"content": content, "reflection": reflection, "next_step": next_step}
        def save():
            pending.stop()
            self.flush_note()
            current = self.store.one("segments", identity)
            if current and dirty:
                data = {key: fields[key].toPlainText() if key in dirty else current[key] for key in fields}
                self.ctrl.save_note(identity, data["content"], data["reflection"], data["next_step"])
                if self.note_id == identity:
                    self.bind_note(identity)
            dirty.clear()
        pending.timeout.connect(save)
        for key, widget in fields.items():
            widget.textChanged.connect(lambda k=key: (dirty.add(k), pending.start(400)))
        form.finished.connect(lambda *_: save())
        form.form.addRow(button("打开完整界面", lambda: (save(), self.reveal(), self.navigate(1), form.accept())))
        self.summary_savers.append(save)
        self.dialogs.append(form)
        form.show()

    def refresh(self):
        day = self.selected_day()
        data = self.store.day(day)
        if day != self.loaded_day:
            self.loaded_day = day
            for widget, key in ((self.day_note, "note"), (self.day_review, "reflection")):
                widget.blockSignals(True)
                widget.setPlainText(data[key])
                widget.blockSignals(False)
        stats, tasks = self.store.stats(day), self.store.tasks(day)
        self.stat_labels[0].setText(minutes(stats["focus"]))
        self.stat_labels[1].setText(str(stats["completed"]))
        self.stat_labels[2].setText(f"{sum(t['status'] == 'done' for t in tasks)}／{len(tasks)}")
        self.tasks_table.blockSignals(True)
        previous = self.task_id()
        self.tasks_table.setRowCount(len(tasks))
        self.tasks_table.ensurePolished()
        self.tasks_table.horizontalHeader().ensurePolished()
        row_height = max(32, self.tasks_table.fontMetrics().height() + 17, self.tasks_table.verticalHeader().minimumSectionSize())
        self.tasks_table.verticalHeader().setDefaultSectionSize(row_height)
        header_height = max(self.tasks_table.horizontalHeader().height(), self.tasks_table.horizontalHeader().sizeHint().height(), self.tasks_table.fontMetrics().height() + 24)
        displayed_rows = min(10, max(3, len(tasks)))
        row_pixels = sum(self.tasks_table.rowHeight(i) for i in range(min(10, len(tasks)))) + max(0, displayed_rows - len(tasks)) * row_height
        default_height = header_height + row_pixels + self.tasks_table.frameWidth() * 2 + 16
        self.tasks_table.setFixedHeight(self.store.get("task_table_height", default_height))
        state_names = {"todo": "", "doing": "（进行中）", "done": "", "carried": "（已带到明天）"}
        for row, t in enumerate(tasks):
            check = QTableWidgetItem("")
            check.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            check.setCheckState(Qt.CheckState.Checked if t["status"] == "done" else Qt.CheckState.Unchecked)
            check.setData(Qt.ItemDataRole.UserRole, t["id"])
            self.tasks_table.setItem(row, 0, check)
            for col, text in enumerate((t["title"] + state_names.get(t["status"], ""), t["notes"],
                                       f"{minutes(t['estimate']) if t['estimate'] else '未设置'}／{self.task_time_text(stats, t['id'])}"), 1):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                self.tasks_table.setItem(row, col, item)
            if t["id"] == previous:
                self.tasks_table.selectRow(row)
        self.tasks_table.blockSignals(False)
        selected = self.ctrl.next_task if self.ctrl.timer.active else self.task_combo.currentData()
        if self.ctrl.timer.active:
            self.selected_tasks = list(self.ctrl.task_ids)
        elif len(self.selected_tasks) == 1:
            selected = self.selected_tasks[0]
        self.task_combo.blockSignals(True)
        self.task_combo.clear()
        self.task_combo.addItem("暂不细分任务" if len(self.selected_tasks) > 1 else "未关联任务", None)
        for t in tasks:
            if t["status"] != "carried" and (not self.selected_tasks or t["id"] in self.selected_tasks):
                self.task_combo.addItem(t["title"], t["id"])
        if selected and self.ctrl.timer.active and self.task_combo.findData(selected) < 0:
            old = self.store.one("tasks", selected)
            if old:
                self.task_combo.addItem(old["title"] + "（其他日期）", selected)
        self.task_combo.setCurrentIndex(max(0, self.task_combo.findData(selected)))
        self.task_combo.blockSignals(False)
        picked = [self.store.one("tasks", identity) for identity in self.selected_tasks]
        names = "、".join(task["title"] for task in picked if task)
        self.tasks_button.setText("关联任务：" + (names if len(names) < 32 else names[:30] + "…") if names else "选择关联任务（可多选）")
        self.tasks_button.setToolTip(names)
        if self.note_id:
            record = self.store.one("segments", self.note_id)
            if record:
                self.note_title.setText(self.record_title(record))
        self.fill_timeline(self.timeline, self.store.segments(day), day)
        upcoming = self.store.rows("SELECT * FROM reminders WHERE enabled=1 ORDER BY due LIMIT 3")
        self.upcoming.setText("即将到来：\n" + "\n".join(f"{parse(r['due']).astimezone():%m-%d %H:%M} · {r['title']}" for r in upcoming) if upcoming else "暂无已启用的提醒或预约")
        self.daily_summary.setText(("当天已结束 · " if data["closed"] else "持续更新 · ") +
            f"专注 {minutes(stats['focus'])} · 休息 {minutes(stats['rest'])} · 提前结束 {stats['partial']} 段\n" +
            "\n".join(f"{title}：{minutes(value)}" for title, value in stats["by_task"].items()))
        self.refresh_history()

    def reveal(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    @staticmethod
    def task_time_text(stats, identity):
        amount = stats["task_seconds"].get(identity, 0)
        if identity in stats["unresolved"]:
            return "未细分" if amount < .001 else minutes(amount) + "＋未细分"
        return minutes(amount)

    def closeEvent(self, event):
        self.flush_note()
        self.flush_day()
        self.flush_summaries()
        self.store.set("geometry", bytes(self.saveGeometry()).hex())
        if self.mini:
            self.store.set("mini_geometry", bytes(self.mini.saveGeometry()).hex())
        if not self.quitting and self.tray and self.store.get("close_to_tray", True):
            event.ignore()
            self.hide()
            if not self.store.get("tray_explained", False):
                self.show_notification("已收至托盘", "计时和提醒继续。通过托盘菜单可以退出。")
                self.store.set("tray_explained", True)
            return
        if not self.quitting:
            self.exit_app()
        event.accept()

    def exit_app(self, save_state=True):
        self.flush_note()
        self.flush_day()
        self.flush_summaries()
        self.tick_timer.stop()
        if save_state:
            if self.ctrl.timer.active:
                self.ctrl.pause()
            self.ctrl.save()
            self.store.set("geometry", bytes(self.saveGeometry()).hex())
            if self.mini:
                self.store.set("mini_geometry", bytes(self.mini.saveGeometry()).hex())
        self.quitting = True
        if self.notification_popup:
            self.notification_popup.dismiss()
        self.audio.close()
        if self.tray:
            self.tray.hide()
        QApplication.instance().quit()
