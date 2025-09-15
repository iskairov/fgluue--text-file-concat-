"""
FGlue: простое GUI-приложение для объединения файлов по шаблонам.

Основные компоненты:
- FileContext: собирает метаданные файла и подставляет их в шаблон
- FGlueApp: GUI (tkinter) для выбора файлов/папок, шаблонов и объединения
"""
import hashlib
import os
import sys
import re
import tkinter as tk
from datetime import datetime
from tkinter import ttk, filedialog, messagebox
from typing import Dict, List, Any
import subprocess


class FileContext:
    """
    Собирает метаданные и умеет подставлять их в шаблон.

    Поддерживаемые плейсхолдеры:
    - {name}, {extension}, {filename}, {path}, {folder}, {drive}, {size}
    - {content}, {lines}, {words}, {chars}, {firstline}, {lastline}
    - {counter} — порядковый номер файла во время прохода
    - {total_files}, {total_lines}, {total_words} — суммарные показатели по всем обработанным файлам
    - {line:N} — N-я строка, {head:N} — первые N строк, {tail:N} — последние N строк
    и др.
    """

    counter_global = 0
    total_files = 0
    total_lines = 0
    total_words = 0
    # Итоговые показатели по всем файлам текущей операции объединения
    grand_total_files = 0
    grand_total_lines = 0
    grand_total_words = 0

    def __init__(self, path: str) -> None:
        """ Читает файл и подготавливает поля для подстановки в шаблон. """

        self.path = path
        self.folder, self.filename = os.path.split(path)
        self.name, self.extension = os.path.splitext(self.filename)
        self.extension = self.extension.lstrip(".")
        self.drive = os.path.splitdrive(path)[0] or ""
        self.size = os.path.getsize(path)

        stat = os.stat(path)
        self.created = datetime.fromtimestamp(stat.st_ctime)
        self.modified = datetime.fromtimestamp(stat.st_mtime)
        self.accessed = datetime.fromtimestamp(stat.st_atime)

        try:
            with open(path, "r", encoding="utf-8") as f:
                self.content = f.read()
        except Exception:
            self.content = ""

        self.lines_list = self.content.splitlines()
        self.lines = len(self.lines_list)
        self.words = len(self.content.split())
        self.chars = len(self.content)

        self.firstline = self.lines_list[0] if self.lines > 0 else ""
        self.lastline = self.lines_list[-1] if self.lines > 0 else ""

        FileContext.counter_global += 1
        self.counter = FileContext.counter_global

        FileContext.total_files += 1
        FileContext.total_lines += self.lines
        FileContext.total_words += self.words

        self.hash_md5 = self._calc_hash("md5")
        self.hash_sha1 = self._calc_hash("sha1")

    def format(self, template: str) -> str:
        """ Возвращает строку: шаблон с подставленными полями и спец. секциями. """

        if "{skip_empty}" in template and self.chars == 0:
            return ""
        if "{skip_nontext}" in template and not self.content.strip():
            return ""

        handle_mapping: Dict[str, Any] = {
            "name": self.name,
            "extension": self.extension,
            "filename": self.filename,
            "path": self.path,
            "folder": os.path.basename(self.folder),
            "drive": self.drive,
            "size": self._human_size(self.size),
            "created": self.created,
            "modified": self.modified,
            "accessed": self.accessed,
            "content": self.content,
            "lines": self.lines,
            "words": self.words,
            "chars": self.chars,
            "firstline": self.firstline,
            "lastline": self.lastline,
            "counter": self.counter,
            "total_files": FileContext.total_files,
            "total_lines": FileContext.total_lines,
            "total_words": FileContext.total_words,
            "grand_total_files": FileContext.grand_total_files,
            "grand_total_lines": FileContext.grand_total_lines,
            "grand_total_words": FileContext.grand_total_words,
            "space": " ",
            "hash:md5": self.hash_md5,
            "hash:sha1": self.hash_sha1,
        }

        cleanup_patterns = [
            r"{skip_empty}",
            r"{skip_nontext}",
            r"{skip_ext:[^}]+}",
            r"{allow_ext:[^}]+}",
            r"{trim_spaces}",
            r"{remove_blank_lines}",
            r"{remove_linebreaks}",
            r"{upper}",
            r"{lower}",
            r"{title}",
        ]

        # ----- Простая подстановка полей вида {name} -----

        result = template
        for key, value in handle_mapping.items():
            result = result.replace("{" + key + "}", str(value))

        # Поддержка форматирования дат: {created:%Y-%m-%d}
        def repl_date(match):
            field, fmt = match.group(1), match.group(2)
            dt = handle_mapping.get(field)
            if isinstance(dt, datetime):
                return dt.strftime(fmt)
            return ""

        result = re.sub(r"{(created|modified|accessed):([^}]+)}", repl_date, result)

        # Спец. плейсхолдеры с параметрами
        for match in re.findall(r"{line:(\d+)}", result):
            n = int(match) - 1
            text = self.lines_list[n] if 0 <= n < self.lines else ""
            result = result.replace(f"{{line:{match}}}", text)

        # {content:numbered} — содержимое с нумерацией строк
        if "{content:numbered}" in result:
            numbered_lines = [f"{i + 1}: {line}" for i, line in enumerate(self.lines_list)]
            result = result.replace("{content:numbered}", "\n".join(numbered_lines))

        # {head:N} — первые N строк файла, объединённые переводами строк
        for match in re.findall(r"{head:(\d+)}", result):
            n = int(match)
            text = "\n".join(self.lines_list[:n])
            result = result.replace(f"{{head:{match}}}", text)

        # {tail:N} — последние N строк файла, объединённые переводами строк
        for match in re.findall(r"{tail:(\d+)}", result):
            n = int(match)
            text = "\n".join(self.lines_list[-n:])
            result = result.replace(f"{{tail:{match}}}", text)

        # {preview:N} — первые N символов содержимого
        for match in re.findall(r"{preview:(\d+)}", result):
            n = int(match)
            text = self.content[:n]
            result = result.replace(f"{{preview:{match}}}", text)

        # {tailpreview:N} — последние N символов содержимого
        for match in re.findall(r"{tailpreview:(\d+)}", result):
            n = int(match)
            text = self.content[-n:]
            result = result.replace(f"{{tailpreview:{match}}}", text)

        # ----- Фильтры по расширениям -----

        # {skip_ext:xxx} — пропустить файл с расширением
        for match in re.findall(r"{skip_ext:([^}]+)}", template):
            if self.extension.lower() == match.lower():
                return ""  # пропускаем файл
            template = template.replace(f"{{skip_ext:{match}}}", "")

        # {allow_ext:xxx} — оставить файл только с расширениями, накопительный эффект
        allow_ext_matches = re.findall(r"{allow_ext:([^}]+)}", template)
        if allow_ext_matches:
            allowed = [m.lower() for m in allow_ext_matches]
            if self.extension.lower() not in allowed:
                return ""  # расширение файла не в списке — пропускаем

        # ----- Очистка и текстовые трансформации

        if "{trim_spaces}" in result:
            lines = result.splitlines()
            # Схлопываем только повторы пробелов/табов (2 и более), не трогаем одиночные и не обрезаем края
            lines = [re.sub(r"[ \t]{2,}", " ", line) for line in lines]
            result = "\n".join(lines)

        if "{remove_blank_lines}" in result:
            lines = [line for line in result.splitlines() if line.strip()]
            result = "\n".join(lines)

        if re.search(r"{remove_linebreaks}", result):
            result = re.sub(r"{remove_linebreaks}", "", result)
            result = re.sub(r"\r?\n+", " ", result)

        if "{upper}" in result:
            result = result.upper()

        if "{lower}" in result:
            result = result.lower()

        if "{title}" in result:
            result = result.title()

        # -----

        # Всегда добавляем пустую строку для {blank_line}
        result = re.sub(r"{blank_line}", "\n", result)

        # -----

        for pattern in cleanup_patterns:
            result = re.sub(pattern, "", result, flags=re.IGNORECASE)

        result = re.sub(r".*\{x}.*\n?", "", result, flags=re.IGNORECASE)

        return result

    @staticmethod
    def _human_size(size: int) -> str:
        """ Форматирует размер файла в удобочитаемый вид с единицами измерения. """

        for unit in ["Б", "КБ", "МБ", "ГБ"]:
            if size < 1024:
                return f"{size:.2f} {unit}"
            size /= 1024
        return f"{size:.2f} ТБ"

    def _calc_hash(self, algo: str) -> str:
        """ Возвращает хэш файла по алгоритму md5 или sha1ю """

        h = hashlib.md5() if algo.lower() == "md5" else hashlib.sha1()
        try:
            with open(self.path, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    h.update(chunk)
            return h.hexdigest()
        except Exception:
            return ""


class FGlueApp:
    """
    Главное GUI‑приложение на tkinter.

    Отвечает за:
    - отображение дерева файлов
    - загрузку/выбор шаблонов
    - выбор файлов (псевдочекбоксы), массовые операции
    - исключение расширений
    - предпросмотр и сохранение результата
    """

    def __init__(self, root: tk.Tk, folder_path: str) -> None:
        """ Создаёт окно, инициализирует состояние и наполняет UI. """

        self.root = root
        self.root.title("FGlue")
        self.root.minsize(640, 480)

        self.folder_path = folder_path
        # Переключатели выбора для каждого пути
        self.check_vars: Dict[str, tk.BooleanVar] = {}
        # Сопоставление путь → id узла для быстрого обновления подписи
        self.path_to_item: Dict[str, str] = {}
        # Шаблоны: ключ — отображаемое имя без расширения, значение — содержимое
        self.templates: Dict[str, str] = {}
        self.selected_template = tk.StringVar()

        # Строка статуса (кол-во выбранных/всего файлов)
        self.status_var = tk.StringVar(value="")

        # Исключённые расширения как множество, например: {".log", ".tmp"}
        self.excluded_exts: set[str] = set()
        self.excluded_exts_var = tk.StringVar(value="")
        # Разрешённые расширения (белый список); пусто — значит разрешены все
        self.included_exts: set[str] = set()
        self.included_exts_var = tk.StringVar(value="")

        self._create_ui()
        self._load_files(self.folder_path)
        self._load_templates()
        self._update_status()
        # Центрируем главное окно после построения интерфейса
        self.root.update_idletasks()
        self._center_window(self.root)

    def _create_ui(self) -> None:
        """ Строит все виджеты и связывает обработчики событий. """

        frame = ttk.Frame(self.root, padding=10)
        frame.pack(fill="both", expand=True)

        # Дерево
        self.tree = ttk.Treeview(frame, columns=("path",), show="tree", selectmode="browse")
        self.tree.pack(fill="both", expand=True, pady=5)

        yscroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=yscroll.set)
        yscroll.pack(side="right", fill="y")

        # Контекстное меню (ПКМ)
        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="Открыть файл", command=self.open_file_selected)
        self.menu.add_command(label="Открыть папку", command=self.open_folder_selected)
        self.menu.add_command(label="Обновить", command=self.refresh_files)
        self.tree.bind("<Button-3>", self._show_context_menu)

        # Блок исключения расширений
        ex_frame = ttk.Frame(frame)
        ex_frame.pack(fill="x", pady=(6, 6))
        ttk.Label(ex_frame, text="Исключить расширения (через запятую):").pack(side="left")
        ex_entry = ttk.Entry(ex_frame, textvariable=self.excluded_exts_var)
        ex_entry.pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(ex_frame, text="Применить", command=self.apply_excluded_exts).pack(side="left")
        ttk.Button(ex_frame, text="Сброс", command=self.reset_excluded_exts).pack(side="left", padx=(6, 0))

        # Блок белого списка расширений
        inc_frame = ttk.Frame(frame)
        inc_frame.pack(fill="x", pady=(0, 6))
        ttk.Label(inc_frame, text="Включить только расширения (через запятую):").pack(side="left")
        inc_entry = ttk.Entry(inc_frame, textvariable=self.included_exts_var)
        inc_entry.pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(inc_frame, text="Применить", command=self.apply_included_exts).pack(side="left")
        ttk.Button(inc_frame, text="Сброс", command=self.reset_included_exts).pack(side="left", padx=(6, 0))

        # Шаблон
        ttk.Label(frame, text="Шаблон объединения:").pack(anchor="w", pady=(10, 0))
        self.template_combo = ttk.Combobox(frame, textvariable=self.selected_template, state="readonly")
        self.template_combo.pack(fill="x", pady=5)

        # Кнопки
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill="x", pady=5)
        # Основные действия справа
        ttk.Button(btn_frame, text="OK", command=self.merge_files).pack(side="right", padx=5)
        ttk.Button(btn_frame, text="Обновить", command=self.refresh_files).pack(side="right")
        ttk.Button(btn_frame, text="Выбрать папку", command=self.choose_folder).pack(side="right")
        # Управление выбором слева
        ttk.Button(btn_frame, text="Выбрать все", command=lambda: self._set_all(True)).pack(side="left")
        ttk.Button(btn_frame, text="Снять все", command=lambda: self._set_all(False)).pack(side="left", padx=5)

        # Строка статуса
        status = ttk.Label(frame, textvariable=self.status_var, anchor="w")
        status.pack(fill="x", pady=(4, 0))

        # Горячие клавиши
        self.root.bind_all("<Control-a>", lambda e: self._on_select_all())
        self.root.bind_all("<Control-d>", lambda e: self._on_deselect_all())
        self.root.bind_all("<F5>", lambda e: self.refresh_files())
        # Переключение чекбокса кликом по элементу (только по тексту, не по индикатору)
        self.tree.bind("<Button-1>", self._on_tree_click, add="+")

        # Тег для подсветки исключённых расширений
        self.tree.tag_configure("excluded_ext", foreground="gray")
        # Тег для подсветки файлов, не попадающих в белый список
        self.tree.tag_configure("not_included_ext", foreground="gray")

    def _center_window(self, win: tk.Tk) -> None:
        """ Центрирует окно на экране, сохраняя текущие размеры. """

        try:
            win.update_idletasks()
            width = win.winfo_width()
            height = win.winfo_height()
            screen_w = win.winfo_screenwidth()
            screen_h = win.winfo_screenheight()
            x = (screen_w - width) // 2
            y = (screen_h - height) // 2
            win.geometry(f"{width}x{height}+{x}+{y}")
        except Exception:
            pass

    def _checkbox_prefix(self, checked: bool) -> str:
        """ Возвращает префикс псевдочекбокса для подписи узла. """

        return "✖ " if checked else "☐ "

    def _normalize_exts(self, raw: str) -> set[str]:
        """ Парсит строку видов: .log, tmp, .bak → {.log, .tmp, .bak} в нижнем регистре. """

        parts = [p.strip().lower() for p in raw.split(",") if p.strip()]
        normalized: list[str] = []
        for p in parts:
            if not p.startswith("."):
                p = "." + p
            normalized.append(p)
        return set(normalized)

    def _is_ext_excluded(self, path: str) -> bool:
        """ True, если расширение файла входит в список исключённых. """

        _, ext = os.path.splitext(path)
        return ext.lower() in self.excluded_exts

    def _is_filtered_out(self, path: str) -> bool:
        """ True, если файл должен быть отфильтрован по расширению. """

        _, ext = os.path.splitext(path)
        ext = ext.lower()
        if ext in self.excluded_exts:
            return True
        if self.included_exts and (ext not in self.included_exts):
            return True
        return False

    def apply_excluded_exts(self) -> None:
        """ Применяет список исключённых расширений: снимает выбор и красит серым. """

        self.excluded_exts = self._normalize_exts(self.excluded_exts_var.get())
        self.apply_extension_filters()

    def apply_included_exts(self) -> None:
        """ Применяет белый список расширений: снимает выбор у неподходящих и красит серым. """

        self.included_exts = self._normalize_exts(self.included_exts_var.get())
        self.apply_extension_filters()

    def apply_extension_filters(self) -> None:
        """ Применяет оба фильтра расширений: исключение и белый список. """

        # Пройтись по всем элементам и обновить состояние/теги
        for path, var in self.check_vars.items():
            item_id = self.path_to_item.get(path)
            if not item_id:
                continue
            if os.path.isdir(path):
                self._refresh_item_label(item_id, path)
                self.tree.item(item_id, tags=())
            else:
                if self._is_filtered_out(path):
                    var.set(False)
                    self._refresh_item_label(item_id, path)
                    _, ext = os.path.splitext(path)
                    ext_l = ext.lower()
                    if ext_l in self.excluded_exts:
                        self.tree.item(item_id, tags=("excluded_ext",))
                    else:
                        self.tree.item(item_id, tags=("not_included_ext",))
                else:
                    self.tree.item(item_id, tags=())
        self._update_status()

    def reset_excluded_exts(self) -> None:
        """ Сбрасывает исключения расширений и очищает подсветку. """

        self.excluded_exts.clear()
        self.excluded_exts_var.set("")
        self.apply_extension_filters()

    def reset_included_exts(self) -> None:
        """ Сбрасывает белый список расширений и очищает подсветку. """

        self.included_exts.clear()
        self.included_exts_var.set("")
        self.apply_extension_filters()

    def _on_tree_click(self, event) -> None:
        """
        Переключает чекбокс у элемента под курсором; директории — рекурсивно.

        Важно: не переключаем, если пользователь кликает по индикатору раскрытия
        или если файл относится к исключённым расширениям.
        """

        item_id = self.tree.identify_row(event.y)
        if not item_id:
            return
        element = self.tree.identify_element(event.x, event.y)
        # Не трогаем, если клик по индикатору/иконке
        if element in ("Treeitem.indicator", "Treeitem.image"):
            return
        abspath = self.tree.item(item_id, "values")[0]
        # Файлы, отфильтрованные по расширению, не переключаем
        if os.path.isfile(abspath) and self._is_filtered_out(abspath):
            return
        var = self.check_vars.get(abspath)
        if var is None:
            return
        new_state = not var.get()
        if os.path.isdir(abspath):
            # Для папки — переключаем всех потомков, но пропускаем отфильтрованные расширения
            self._set_state_recursive(item_id, new_state)
        else:
            self._set_item_state(item_id, abspath, new_state)
        self._update_status()

    def _refresh_item_label(self, item_id: str, abspath: str) -> None:
        """ Обновляет подпись узла (префикс чекбокса + имя файла/папки). """

        base = os.path.basename(abspath)
        checked = bool(self.check_vars.get(abspath, tk.BooleanVar(value=True)).get())
        self.tree.item(item_id, text=self._checkbox_prefix(checked) + base)

    def _set_item_state(self, item_id: str, abspath: str, state: bool) -> None:
        """ Устанавливает состояние выбора для одного узла и обновляет подпись. """

        var = self.check_vars.get(abspath)
        if var is not None:
            var.set(state)
        self._refresh_item_label(item_id, abspath)

    def _set_state_recursive(self, item_id: str, state: bool) -> None:
        """
        Рекурсивно меняет состояние узла и всех его потомков.

        Исключённые по расширению файлы пропускаются.
        """

        abspath = self.tree.item(item_id, "values")[0]
        # Пропускаем файлы, отфильтрованные по расширению
        if os.path.isfile(abspath) and self._is_filtered_out(abspath):
            return
        self._set_item_state(item_id, abspath, state)
        for child in self.tree.get_children(item_id):
            self._set_state_recursive(child, state)

    def _set_all(self, state: bool) -> None:
        """ Глобально включает/выключает выбор у всех элементов, кроме отфильтрованных. """

        for path, var in self.check_vars.items():
            # Отфильтрованные расширения всегда остаются снятыми
            if os.path.isfile(path) and self._is_filtered_out(path):
                var.set(False)
            else:
                var.set(state)
            item_id = self.path_to_item.get(path)
            if item_id:
                # Теги не меняем здесь — ими управляет apply/reset
                self._refresh_item_label(item_id, path)
        self._update_status()

    def _on_select_all(self) -> None:
        """ Ctrl+A: выбрать все. """

        self._set_all(True)

    def _on_deselect_all(self) -> None:
        """ Ctrl+D: снять выделение со всех. """

        self._set_all(False)

    def _show_context_menu(self, event) -> None:
        """ Показывает контекстное меню и выделяет элемент под курсором. """

        try:
            item_id = self.tree.identify_row(event.y)
            if item_id:
                self.tree.selection_set(item_id)
                self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def open_file_selected(self) -> None:
        """ Открывает выделенный путь: файл или директорию средствами ОС. """

        sel = self.tree.selection()
        if not sel:
            return
        abspath = self.tree.item(sel[0], "values")[0]
        path = abspath
        self._open_in_os(path)

    def open_folder_selected(self) -> None:
        """ Открывает папку, где лежит файл; если выбрана папка — открывает её. """

        sel = self.tree.selection()
        if not sel:
            return
        abspath = self.tree.item(sel[0], "values")[0]
        folder = abspath if os.path.isdir(abspath) else os.path.dirname(abspath)
        if not folder:
            folder = os.path.dirname(abspath)
        self._open_in_os(folder)

    def _open_in_os(self, path: str) -> None:
        """ Открывает путь в ОС (Windows/macOS/Linux). """

        try:
            if sys.platform.startswith("win"):
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.run(["open", path], check=False)
            else:
                subprocess.run(["xdg-open", path], check=False)
        except Exception as exc:
            messagebox.showerror("Ошибка", f"Не удалось открыть: {path}\n{exc}")

    def _load_files(self, path: str) -> None:
        """ Перезаполняет дерево файлов, начиная с указанного пути. """

        self.tree.delete(*self.tree.get_children())
        self.check_vars.clear()
        self.path_to_item.clear()

        def insert_node(parent: str, abspath: str) -> None:
            """ Вставляет в дерево узел для пути и рекурсивно добавляет потомков. """

            name = os.path.basename(abspath)
            # по умолчанию все выбраны
            self.check_vars[abspath] = tk.BooleanVar(value=True)
            label = self._checkbox_prefix(True) + name
            node = self.tree.insert(parent, "end", text=label, open=(parent == ""), values=(abspath,))
            self.path_to_item[abspath] = node

            if os.path.isdir(abspath):
                for item in sorted(os.listdir(abspath)):
                    insert_node(node, os.path.join(abspath, item))

        insert_node("", path)
        # Если уже заданы фильтры расширений, применим их
        if self.excluded_exts or self.included_exts:
            self.apply_extension_filters()
        else:
            self._update_status()

    def get_selected_files(self) -> List[str]:
        """ Возвращает список путей файлов, которые отмечены галочкой и не исключены. """

        selected: List[str] = []

        def walk(node: str) -> None:
            for child in self.tree.get_children(node):
                abspath = self.tree.item(child, "values")[0]
                if os.path.isdir(abspath):
                    walk(child)
                else:
                    # Отфильтрованные расширения — всегда пропускаем
                    if self._is_filtered_out(abspath):
                        continue
                    var = self.check_vars.get(abspath)
                    if var is None or var.get():
                        selected.append(abspath)

        walk("")
        return selected

    def _update_status(self) -> None:
        """ Обновляет строку статуса: выбрано/всего файлов. """

        total_files = 0
        def count_files(node: str) -> None:
            nonlocal total_files
            for child in self.tree.get_children(node):
                abspath = self.tree.item(child, "values")[0]
                if os.path.isdir(abspath):
                    count_files(child)
                else:
                    total_files += 1
        count_files("")
        selected_files = len(self.get_selected_files())
        self.status_var.set(f"Выбрано файлов: {selected_files} / {total_files}")

    def _load_templates(self) -> None:
        """ Загружает шаблоны из папки templates/ и подготавливает список выбора. """

        templates_dir = "templates"
        os.makedirs(templates_dir, exist_ok=True)

        if not os.listdir(templates_dir):
            with open(os.path.join(templates_dir, "1. Содержимой с шапкой.txt"), "w", encoding="utf-8") as f:
                f.write("----- {filename} -----{blank_line}{content}{blank_line}")
            with open(os.path.join(templates_dir, "2. Cодержимое с шапкой и нумерацией.txt"), "w", encoding="utf-8") as f:
                f.write("----- {counter}. {filename} -----{blank_line}{content:numbered}{blank_line}")
            with open(os.path.join(templates_dir, "3. Только содержимое.txt"), "w", encoding="utf-8") as f:
                f.write("{content}{blank_line}")
            with open(os.path.join(templates_dir, "4. Cодержимое в одну строку.txt"), "w", encoding="utf-8") as f:
                f.write("{trim_spaces}{remove_linebreaks}{content}{space}")
            with open(os.path.join(templates_dir, "5. Объединение программного кода.txt"), "w", encoding="utf-8") as f:
                f.write(
                    "{allow_ext:py}{x}\n"
                    "{allow_ext:js}{x}\n"
                    "{allow_ext:ts}{x}\n"
                    "{allow_ext:php}{x}\n"
                    "{allow_ext:html}{x}\n"
                    "{allow_ext:css}{x}\n"
                    "{allow_ext:java}{x}\n"
                    "{allow_ext:cpp}{x}\n"
                    "{allow_ext:c}{x}\n"
                    "{allow_ext:cs}{x}\n"
                    "{allow_ext:rb}{x}\n"
                    "{allow_ext:go}{x}\n"
                    "{allow_ext:rs}{x}\n"
                    "{allow_ext:swift}{x}\n"
                    "{allow_ext:kt}{x}\n"
                    "{allow_ext:sql}{x}\n"
                    "{x}\n"
                    "{allow_ext:json}{x}\n"
                    "{allow_ext:xml}{x}\n"
                    "{x}\n"
                    "{allow_ext:md}{x}\n"
                    "{x}\n"
                    "----- {filename} ({path}) -----\n"
                    "{remove_blank_lines}{content}{blank_line}{blank_line}"
                )
            with open(os.path.join(templates_dir, "6. Информация о файлах.txt"), "w", encoding="utf-8") as f:
                f.write(
                    "{show_before}Информация о файлах:\n"
                    "{show_before}----------------------------------\n"
                    "{show_before}Всего файлов: {grand_total_files}\n"
                    "{show_before}Всего строк: {grand_total_lines}\n"
                    "{show_before}Всего слов: {grand_total_words}\n"
                    "{show_before}----------------------------------\n"
                    "{counter}. Файл: {filename}\n"
                    "    Расширение: {extension}\n"
                    "    Путь: {path}\n"
                    "    Размер: {size}\n"
                    "    Строк: {lines}, Слов: {words}, Символов: {chars}\n"
                    "    Дата создания: {created:%d.%m.%Y %H:%M:%S}\n"
                    "    Дата изменения: {modified:%d.%m.%Y %H:%M:%S}\n"
                    "    Последний доступ: {accessed:%d.%m.%Y %H:%M:%S}\n"
                    "    Хэш (MD5): {hash:md5}\n"
                    "    Хэш (SHA1): {hash:sha1}\n"
                    "\n"
                    "{show_after}----------------------------------\n"
                )

        # Загружаем файлы шаблонов: ключ — имя без расширения
        self.templates.clear()
        for fname in os.listdir(templates_dir):
            fpath = os.path.join(templates_dir, fname)
            if os.path.isfile(fpath):
                name_wo_ext, _ = os.path.splitext(fname)
                display = name_wo_ext
                # Если имя без расширения уже занято, делаем уникальным
                while display in self.templates:
                    display += "_"
                with open(fpath, "r", encoding="utf-8") as f:
                    self.templates[display] = f.read()

        self.template_combo["values"] = list(self.templates.keys())
        if self.templates:
            self.template_combo.current(0)

    def choose_folder(self) -> None:
        """ Открывает диалог выбора папки и перезагружает дерево файлов. """

        new_path = filedialog.askdirectory()
        if new_path:
            self.folder_path = new_path
            self._load_files(new_path)
            self._update_status()

    def refresh_files(self) -> None:
        """ Обновляет содержимое дерева текущей папки и повторно применяет фильтры. """

        if not self.folder_path:
            return
        self._load_files(self.folder_path)
        self._update_status()

    def merge_files(self) -> None:
        """ Формирует объединённый текст по текущему шаблону и выбранным файлам. """

        FileContext.counter_global = 0
        FileContext.total_files = 0
        FileContext.total_lines = 0
        FileContext.total_words = 0
        FileContext.grand_total_files = 0
        FileContext.grand_total_lines = 0
        FileContext.grand_total_words = 0
        
        template_name = self.selected_template.get()
        if not template_name:
            messagebox.showwarning("Внимание", "Выберите шаблон!")
            return

        template = self.templates[template_name]

        # {limit_files:N} — ограничить кол-во файлов для объединения
        limit = None
        for match in re.findall(r"{limit_files:(\d+)}", template):
            try:
                limit = int(match)
            except Exception:
                limit = None
        if limit is not None and limit < 0:
            limit = 0

        # Уберём плейсхолдер из шаблона, чтобы он не попал в результат
        template = re.sub(r"{limit_files:\d+}", "", template)
        result = ""

        selected_files = self.get_selected_files()
        if limit is not None:
            selected_files = selected_files[:limit]

        # Подсчитаем итоговые значения по всем выбранным файлам заранее
        FileContext.grand_total_files = len(selected_files)
        total_lines_sum = 0
        total_words_sum = 0
        for p in selected_files:
            try:
                with open(p, "r", encoding="utf-8") as f:
                    content_tmp = f.read()
            except Exception:
                content_tmp = ""
            total_lines_sum += len(content_tmp.splitlines())
            total_words_sum += len(content_tmp.split())
        FileContext.grand_total_lines = total_lines_sum
        FileContext.grand_total_words = total_words_sum

        # ----- Объединение файлов -----

        for i, path in enumerate(selected_files):
            ctx = FileContext(path)
            temp_template = template

            if i == 0:
                # Первый файл: убираем show_after
                temp_template = re.sub(r".*\{show_after\}.*\n?", "", temp_template)
                temp_template = re.sub(r"{show_before}", "", temp_template)
            elif i == len(selected_files) - 1:
                # Последний файл: убираем show_before
                temp_template = re.sub(r"{show_after}", "", temp_template)
                temp_template = re.sub(r".*\{show_before\}.*\n?", "", temp_template)
            else:
                # "Средние" файлы: убираем и show_before, и show_after
                temp_template = re.sub(r".*\{show_before\}.*\n?", "", temp_template)
                temp_template = re.sub(r".*\{show_after\}.*\n?", "", temp_template)

            result += ctx.format(temp_template)

        self._show_preview(result)

    def _show_preview(self, result: str) -> None:
        """ Отображает окно предпросмотра с возможностью копирования/сохранения. """

        preview = tk.Toplevel(self.root)
        preview.title("Результат объединения")
        preview.minsize(600, 400)

        # Панель инструментов
        toolbar = ttk.Frame(preview)
        toolbar.pack(fill="x", padx=5, pady=5)

        # Кнопки действий
        ttk.Button(toolbar, text="Скопировать", command=lambda: self.copy_to_clipboard(result)).pack(side="left", padx=2)
        ttk.Button(toolbar, text="Сохранить", command=lambda: self.save_result(result)).pack(side="left", padx=2)
        
        # Разделитель
        ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=10)
        
        # Управление шрифтом
        ttk.Label(toolbar, text="Размер:").pack(side="left", padx=(0, 2))
        font_size = tk.IntVar(value=10)
        font_combo = ttk.Combobox(toolbar, textvariable=font_size, values=[8, 9, 10, 11, 12, 14, 16, 18, 20], width=5, state="readonly")
        font_combo.pack(side="left", padx=2)

        # Контейнер для текста с нумерацией строк
        text_frame = ttk.Frame(preview)
        text_frame.pack(fill="both", expand=True, padx=5, pady=(0, 5))

        # Вертикальная прокрутка
        yscroll = ttk.Scrollbar(text_frame, orient="vertical")
        yscroll.pack(side="right", fill="y")

        # Горизонтальная прокрутка
        xscroll = ttk.Scrollbar(text_frame, orient="horizontal")
        xscroll.pack(side="bottom", fill="x")

        # Текстовое поле
        text = tk.Text(text_frame, wrap="word", yscrollcommand=yscroll.set, xscrollcommand=xscroll.set, font=("Consolas", 10), tabs=4)
        text.insert("1.0", result)
        text.pack(side="left", fill="both", expand=True)
        
        # Связываем скроллбары
        yscroll.configure(command=text.yview)
        xscroll.configure(command=text.xview)
        
        # Делаем текст только для чтения
        text.config(state="disabled")

        # Обработчики для динамического изменения
        def update_font():
            text.config(font=("Consolas", font_size.get()))
            text.config(state="normal")
            text.delete("1.0", "end")
            text.insert("1.0", result)
            text.config(state="disabled")

        font_combo.bind("<<ComboboxSelected>>", lambda e: update_font())

        # Статистика внизу
        stats_frame = ttk.Frame(preview)
        stats_frame.pack(fill="x", padx=5, pady=(0, 5))
        
        lines_count = len(result.splitlines())
        chars_count = len(result)
        words_count = len(result.split())
        
        stats_text = f"Строк: {lines_count} | Символов: {chars_count} | Слов: {words_count}"
        ttk.Label(stats_frame, text=stats_text, font=("TkDefaultFont", 8)).pack(side="left")

        # Центрируем окно предпросмотра после раскладки
        preview.update_idletasks()
        self._center_window(preview)

    def copy_to_clipboard(self, result: str) -> None:
        """ Копирует текст в буфер обмена и показывает уведомление. """

        self.root.clipboard_clear()
        self.root.clipboard_append(result)
        self.root.update()
        messagebox.showinfo("Готово", "Текст скопирован в буфер обмена!")

    def save_result(self, result: str) -> None:
        """ Сохраняет результат в текстовый файл через диалог выбора пути. """

        save_path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Текстовые файлы", "*.txt")]
        )
        if save_path:
            with open(save_path, "w", encoding="utf-8") as f:
                f.write(result)
            messagebox.showinfo("Готово", f"Файл сохранён: {save_path}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        folder = sys.argv[1]
    else:
        folder = filedialog.askdirectory(title="Выберите папку")

    if folder:
        root = tk.Tk()
        app = FGlueApp(root, folder)
        root.mainloop()
