from __future__ import annotations

import concurrent.futures
from typing import TypeVar, Callable, Type

from rich.style import Style
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import RichLog, Button, Input, Static, DataTable
from textual.containers import Horizontal, Vertical

from src.log import TextualLogHandler

T = TypeVar("T")


MENU_BUTTON_IDS = {"menu-clientes", "menu-facturas", "menu-generar", "menu-validar", "quit"}


class FacturadorApp(App):
    TITLE = "Facturador ARCA"

    def __init__(self) -> None:
        super().__init__()
        self._worker_fn = None
        self._confirm_future: concurrent.futures.Future[bool] | None = None
        self._input_future: concurrent.futures.Future[str] | None = None
        self._table_close_future: concurrent.futures.Future[None] | None = None
        self._menu_future: concurrent.futures.Future[str] | None = None
        self._table_snapshots: list[tuple] = []

    def compose(self) -> ComposeResult:
        yield HomeMenu()
        yield RichLog(highlight=False, markup=False, wrap=False)
        yield ConfirmBar()
        yield InputBar()

    def on_mount(self) -> None:
        self.toggle_log(show=False)
        if self._worker_fn is not None:
            self.run_worker(self._worker_fn, thread=True)

    def on_unmount(self) -> None:
        for future in [self._menu_future, self._confirm_future, self._input_future, self._table_close_future]:
            if future and not future.done():
                future.set_exception(SystemExit())

    def set_worker(self, fn: Callable) -> None:
        self._worker_fn = fn

    def get_log_handler(self) -> TextualLogHandler:
        return TextualLogHandler(
            lambda text: self.call_from_thread(self.query_one(RichLog).write, text)
        )

    def _toggle(self, widget_type: type, show: bool | None, **kwargs) -> None:
        widget = self.query_one(widget_type)
        if show is None:
            show = not widget.display

        if show and kwargs:
            assert hasattr(widget, "show")
            widget.show(**kwargs)
        else:
            widget.display = show

    def toggle_home_menu(self, show: bool | None = None) -> None:
        self._toggle(HomeMenu, show=show)

    def toggle_log(self, show: bool | None = None) -> None:
        self._toggle(RichLog, show=show)

    def toggle_confirm_bar(self, show: bool | None = None, **kwargs) -> None:
        self._toggle(ConfirmBar, show=show, **kwargs)

    def toggle_input_bar(self, show: bool | None = None, **kwargs) -> None:
        self._toggle(InputBar, show=show, **kwargs)

    def wait_for_menu_sync(self) -> str:
        self._menu_future = concurrent.futures.Future()
        self.call_from_thread(self.toggle_log, show=False)
        self.call_from_thread(self.toggle_home_menu, show=True)
        return self._menu_future.result()

    def ask_sync(self, prompt: str, yes_label: str | None = "Sí", no_label: str | None = "No") -> bool:
        self._confirm_future = concurrent.futures.Future()
        self.call_from_thread(self.toggle_confirm_bar, show=True, prompt=prompt, yes_label=yes_label, no_label=no_label)
        return self._confirm_future.result()

    def input_sync(self, prompt: str, type: Type[T], parser: Callable[[str], T]) -> T:
        while True:
            self._input_future = concurrent.futures.Future()
            self.call_from_thread(self.toggle_input_bar, show=True, prompt=prompt)
            raw = self._input_future.result()
            try:
                value = parser(raw)
                if isinstance(value, type):
                    return value
            except Exception:
                pass

    def show_table_sync(
        self,
        label: str,
        columns: list[str],
        rows: list[tuple],
        sub_columns: list[str] | None = None,
        sub_rows: list[list[tuple]] | None = None,
        link: bool = True
    ) -> None:
        self._table_close_future = concurrent.futures.Future()
        modal = TableModal(label, columns, list(rows), list(sub_columns or []), list(sub_rows or []))
        self.call_from_thread(self.push_screen, modal, lambda _: self._table_close_future.set_result(None))  # pyright: ignore[reportOptionalMemberAccess]
        self._table_close_future.result()  # espera dismiss del modal
        if link:
            snapshot_id = len(self._table_snapshots)
            self._table_snapshots.append((label, columns, list(rows), list(sub_columns or []), list(sub_rows or [])))
            link_style = Style(bold=True, color="cyan", underline=True, meta={"@click": f"app.link_table('{snapshot_id}')"})
            self.call_from_thread(self.query_one(RichLog).write, Text.assemble("  ↗ ", (label, link_style)))

    def action_link_table(self, snapshot_id: str) -> None:
        label, cols, rows, sub_cols, sub_rows = self._table_snapshots[int(snapshot_id)]
        self.push_screen(TableModal(label, cols, rows, sub_cols, sub_rows))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id in MENU_BUTTON_IDS:
            if self._menu_future and not self._menu_future.done():
                self.query_one(RichLog).clear()
                self.call_later(self.toggle_home_menu, show=False)
                self.call_later(self.toggle_log, show=True)
                self._menu_future.set_result(event.button.id)
            return
        if self._confirm_future and not self._confirm_future.done():
            self._confirm_future.set_result(event.button.id == "yes")
        self.toggle_confirm_bar(show=False)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if self._input_future and not self._input_future.done():
            self._input_future.set_result(event.value)
        self.toggle_input_bar(show=False)


class InputBar(Widget):
    DEFAULT_CSS = """
    InputBar {
        height: auto;
        border-top: solid $primary;
        padding: 1 2;
        display: none;
    }
    InputBar Static {
        height: auto;
        margin-bottom: 1;
        text-align: center;
    }
    InputBar Input {
        width: 100%;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("", id="input-prompt", markup=True)
        yield Input(id="input-field")

    def show(self, prompt: str) -> None:
        self.query_one("#input-prompt", Static).update(prompt)
        input_widget = self.query_one("#input-field", Input)
        input_widget.clear()
        self.display = True
        input_widget.focus()


class ConfirmBar(Widget):
    DEFAULT_CSS = """
    ConfirmBar {
        height: auto;
        border-top: solid $primary;
        padding: 1 2;
        display: none;
    }
    ConfirmBar #prompt-container {
        height: auto;
        align: center middle;
        margin-bottom: 1;
    }
    ConfirmBar Static {
        height: auto;
        width: auto;
        text-align: left;
    }
    ConfirmBar Horizontal {
        height: auto;
        align: center middle;
    }
    ConfirmBar Button {
        margin-right: 1;
    }
    """

    def compose(self) -> ComposeResult:
        with Horizontal(id="prompt-container"):
            yield Static("", id="confirm-prompt", markup=True)
        with Horizontal():
            yield Button("Sí", id="yes", variant="success")
            yield Button("No", id="no", variant="error")

    def show(self, prompt: str, yes_label: str | None = "Sí", no_label: str | None = "No") -> None:
        self.query_one("#confirm-prompt", Static).update(prompt)
        yes_btn = self.query_one("#yes", Button)
        no_btn = self.query_one("#no", Button)
        if yes_label is not None:
            yes_btn.label = yes_label
            yes_btn.display = True
        else:
            yes_btn.display = False
        if no_label is not None:
            no_btn.label = no_label
            no_btn.display = True
        else:
            no_btn.display = False
        self.display = True


class HomeMenu(Widget):
    DEFAULT_CSS = """
    HomeMenu {
        height: 1fr;
        align: center middle;
    }
    HomeMenu Vertical {
        height: auto;
        width: 60;
        align: center middle;
    }
    HomeMenu Static {
        height: auto;
        margin-bottom: 2;
        text-align: center;
        text-style: bold;
    }
    HomeMenu Button {
        width: 30;
        margin: 0 15 1 15;
    }
    HomeMenu #menu-tip {
        width: 60;
        margin-top: 1;
        text-style: italic dim;
        text-align: center;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Menu")
            yield Button("Ver clientes", id="menu-clientes", variant="primary")
            yield Button("Ver facturas", id="menu-facturas", variant="primary")
            yield Button("Generar facturas", id="menu-generar", variant="success")
            yield Button("Validar PDF", id="menu-validar", variant="warning")
            yield Button("Salir (Ctrl+Q)", id="quit", variant="error")
            yield Static("Tip: mantene Shift presionado y arrastra el mouse haciendo click para seleccionar texto", id="menu-tip")


class TableModal(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss", show=False)]
    DEFAULT_CSS = """
    TableModal {
        align: center middle;
        background: $background 80%;
    }
    TableModal > Vertical {
        width: 100%;
        height: 100%;
        background: $surface;
        border: thick $primary;
    }
    TableModal DataTable {
        height: 1fr;
    }
    """

    def __init__(self, label: str, columns: list[str], rows: list[tuple], sub_columns: list[str], sub_rows: list[list[tuple]]) -> None:
        super().__init__()
        self._label = label
        self._columns = columns
        self._rows = rows
        self._sub_columns = sub_columns
        self._sub_rows = sub_rows
        self._sub_rows_map: dict[str, list[tuple]] = {}
        self._sort_col: int | None = None
        self._sort_reverse: bool = False

    def compose(self) -> ComposeResult:
        with Vertical():
            yield DataTable(zebra_stripes=True, cursor_type="row")

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns(*self._columns)
        if self._sub_rows:
            assert len(self._rows) == len(self._sub_rows)
        for i, row in enumerate(self._rows):
            key = str(i)
            table.add_row(*row, key=key)
            if self._sub_rows and self._sub_rows[i]:
                self._sub_rows_map[key] = self._sub_rows[i]

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        key = event.row_key.value
        if key in self._sub_rows_map:
            self.app.push_screen(TableSubrowView(self._sub_columns, self._sub_rows_map[key]))

    def on_data_table_header_selected(self, event: DataTable.HeaderSelected) -> None:
        col = event.column_index
        if self._sort_col == col:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_col = col
            self._sort_reverse = False

        def key(value) -> tuple:
            s = str(value).replace("$", "").replace(",", "").replace("%", "").strip()
            try:
                return (0, float(s))
            except ValueError:
                return (1, s)

        event.data_table.sort(event.column_key, key=key, reverse=self._sort_reverse)


class TableSubrowView(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss", show=False)]
    DEFAULT_CSS = """
    TableSubrowView {
        align: center middle;
    }
    TableSubrowView > Vertical {
        width: 80%;
        height: auto;
        max-height: 60%;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }
    TableSubrowView DataTable {
        height: auto;
        max-height: 1fr;
    }
    """

    def __init__(self, columns: list[str], rows: list[tuple]) -> None:
        super().__init__()
        self._columns = columns
        self._rows = rows

    def compose(self) -> ComposeResult:
        with Vertical():
            yield DataTable(zebra_stripes=True)

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns(*self._columns)
        for row in self._rows:
            table.add_row(*row)
