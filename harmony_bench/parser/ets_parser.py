from __future__ import annotations
from typing import List, Optional, Tuple
import pydantic
import tree_sitter_typescript as tsts
from tree_sitter import Language, Parser, Node

# NOTE: tree-sitter 0.21.3's installed binding requires the two-arg
# Language(ptr, name) constructor, not the single-arg form shown in the brief.
_LANG = Language(tsts.language_tsx(), "tsx")
_PARSER = Parser()
_PARSER.set_language(_LANG)


class ExportSymbol(pydantic.BaseModel):
    name: str
    kind: str
    start_byte: int
    end_byte: int
    start_point: Tuple[int, int]
    end_point: Tuple[int, int]


class FunctionDef(pydantic.BaseModel):
    name: str
    body_start_byte: int
    body_end_byte: int
    start_point: Tuple[int, int]
    end_point: Tuple[int, int]


class StructDef(pydantic.BaseModel):
    name: str
    is_component: bool
    build_body_start_byte: Optional[int] = None
    build_body_end_byte: Optional[int] = None
    decl_start_byte: int = 0       # whole `[@Component] struct X { ... }` span
    decl_end_byte: int = 0
    start_point: Tuple[int, int]
    end_point: Tuple[int, int]


class ImportStmt(pydantic.BaseModel):
    module: str
    names: List[str]
    is_type_only: bool = False
    default_name: Optional[str] = None   # `import X from 'mod'` -> X binds mod's default


class ReExport(pydantic.BaseModel):
    # `export { orig as name } from 'module'`  (name==orig when no alias);
    # is_default marks `export { default as name } from 'module'`.
    name: str
    orig: str
    module: str
    is_default: bool = False


class ParsedFile(pydantic.BaseModel):
    path: str
    source: str
    exports: List[ExportSymbol] = []
    functions: List[FunctionDef] = []
    structs: List[StructDef] = []
    imports: List[ImportStmt] = []
    default_export_name: Optional[str] = None   # `export default <X>` -> canonical name
    star_reexports: List[str] = []              # `export * from 'module'` module specs
    named_reexports: List[ReExport] = []        # `export { a as b } from 'module'`
    had_errors: bool = False


def _text(src: bytes, node: Node) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", "replace")


def _has_error(node: Node) -> bool:
    if node.is_error or node.is_missing:
        return True
    stack = [node]
    while stack:
        n = stack.pop()
        if n.is_error or n.is_missing:
            return True
        stack.extend(n.children)
    return False


def parse_source(source: str, path: str = "<mem>") -> ParsedFile:
    src = source.encode("utf-8")
    tree = _PARSER.parse(src)
    root = tree.root_node
    pf = ParsedFile(path=path, source=source, had_errors=_has_error(root))

    def name_of(decl: Node) -> Optional[str]:
        n = decl.child_by_field_name("name")
        return _text(src, n) if n is not None else None

    def visit(node: Node, exported: bool):
        for child in node.named_children:
            t = child.type
            if t == "export_statement":
                if _route_reexport(src, child, pf):
                    continue
                visit(child, True)
            elif t == "function_declaration":
                nm = name_of(child)
                body = child.child_by_field_name("body")
                if nm and body is not None:
                    pf.functions.append(FunctionDef(
                        name=nm, body_start_byte=body.start_byte, body_end_byte=body.end_byte,
                        start_point=child.start_point, end_point=child.end_point))
                if exported and nm:
                    pf.exports.append(ExportSymbol(name=nm, kind="function",
                        start_byte=child.start_byte, end_byte=child.end_byte,
                        start_point=child.start_point, end_point=child.end_point))
            elif t in ("class_declaration",):
                nm = name_of(child)
                if exported and nm:
                    pf.exports.append(ExportSymbol(name=nm, kind="class",
                        start_byte=child.start_byte, end_byte=child.end_byte,
                        start_point=child.start_point, end_point=child.end_point))
            elif t in ("enum_declaration", "interface_declaration",
                       "type_alias_declaration", "abstract_class_declaration"):
                nm = name_of(child)
                if exported and nm:
                    kind = {"enum_declaration": "enum",
                            "interface_declaration": "interface",
                            "type_alias_declaration": "type",
                            "abstract_class_declaration": "class"}[t]
                    pf.exports.append(ExportSymbol(name=nm, kind=kind,
                        start_byte=child.start_byte, end_byte=child.end_byte,
                        start_point=child.start_point, end_point=child.end_point))
            elif t in ("lexical_declaration", "variable_declaration") and exported:
                for d in child.named_children:
                    if d.type == "variable_declarator":
                        nm = name_of(d)
                        if nm:
                            pf.exports.append(ExportSymbol(name=nm, kind="const",
                                start_byte=child.start_byte, end_byte=child.end_byte,
                                start_point=child.start_point, end_point=child.end_point))
            elif t == "export_clause" or t == "export_specifier":
                for spec in child.named_children if t == "export_clause" else [child]:
                    alias = spec.child_by_field_name("alias")
                    nm = alias if alias is not None else spec.child_by_field_name("name")
                    if nm is not None:
                        pf.exports.append(ExportSymbol(name=_text(src, nm), kind="reexport",
                            start_byte=spec.start_byte, end_byte=spec.end_byte,
                            start_point=spec.start_point, end_point=spec.end_point))
            elif t == "import_statement":
                _collect_import(src, child, pf)
            else:
                # ArkUI struct shows up under various node types depending on grammar;
                # struct extraction is handled by the lexical fallback scanner below.
                visit(child, exported)

    visit(root, False)
    _collect_structs_fallback(src, root, pf)
    return pf


def _collect_import(src: bytes, node: Node, pf: ParsedFile) -> None:
    is_type = any(c.type == "type" for c in node.children)
    src_node = node.child_by_field_name("source")
    module = _text(src, src_node).strip("'\"") if src_node is not None else ""
    names: List[str] = []
    default_name: Optional[str] = None
    for clause in node.named_children:
        if clause.type == "import_clause":
            for sub in clause.named_children:
                if sub.type == "named_imports":
                    for spec in sub.named_children:
                        nm = spec.child_by_field_name("name")
                        if nm is not None:
                            names.append(_text(src, nm))
                elif sub.type == "identifier":
                    # `import X from 'mod'` -> X binds mod's default export
                    default_name = _text(src, sub)
    if module:
        pf.imports.append(ImportStmt(module=module, names=names,
                                     is_type_only=is_type, default_name=default_name))


_IMPORT_RE = None


def _route_reexport(src: bytes, node: Node, pf: ParsedFile) -> bool:
    """Handle `export default X`, `export * from 'mod'`, and `export {..} from 'mod'`.
    Returns True if the statement was consumed (caller should not recurse)."""
    import re
    text = _text(src, node)
    src_node = node.child_by_field_name("source")
    module = _text(src, src_node).strip("'\"") if src_node is not None else None

    # export * from 'mod'   /   export * as ns from 'mod'
    if module and re.match(r"export\s+\*", text):
        pf.star_reexports.append(module)
        return True

    # export { a, b as c, default as d } from 'mod'
    if module and "{" in text:
        inner = text[text.find("{") + 1:text.find("}")]
        for part in inner.split(","):
            part = part.strip()
            if not part:
                continue
            m = re.match(r"([A-Za-z_$][\w$]*)(?:\s+as\s+([A-Za-z_$][\w$]*))?", part)
            if not m:
                continue
            orig = m.group(1)
            name = m.group(2) or orig
            pf.named_reexports.append(ReExport(
                name=name, orig=orig, module=module, is_default=(orig == "default")))
        return True

    # export default <expr|class|function>
    if re.match(r"export\s+default\b", text):
        body = text[text.find("default") + len("default"):]
        m = (re.search(r"\bclass\s+([A-Za-z_$][\w$]*)", body)
             or re.search(r"\bfunction\s+([A-Za-z_$][\w$]*)", body)
             or re.search(r"\bnew\s+([A-Za-z_$][\w$]*)", body)
             or re.match(r"\s*([A-Za-z_$][\w$]*)\s*$", body))
        pf.default_export_name = m.group(1) if m else None
        return True

    return False


def _collect_structs_fallback(src: bytes, root: Node, pf: ParsedFile) -> None:
    text = src.decode("utf-8", "replace")
    # Walk all nodes; ArkUI `struct X {...}` parses as an ERROR or as identifier-led
    # block in tsx grammar. Find "struct <Name>" lexically, then locate build() body.
    import re
    for m in re.finditer(r'(?:@Component\b[\s\S]{0,40}?)?\bstruct\s+([A-Za-z_]\w*)', text):
        name = m.group(1)
        decl_start = m.start()
        # The optional @Component group may be captured inside the match, so
        # scan from 60 chars before the match through the struct name to detect it.
        is_comp = "@Component" in text[max(0, decl_start - 60):m.start(1)]
        # find the struct body braces
        brace = text.find("{", m.end())
        if brace == -1:
            continue
        body_end = _match_brace(text, brace)
        struct_src = text[brace:body_end]
        b_start = b_end = None
        bm = re.search(r'\bbuild\s*\(\s*\)\s*\{', struct_src)
        if bm is not None:
            open_idx = brace + bm.end() - 1
            close_idx = _match_brace(text, open_idx)
            b_start, b_end = len(text[:open_idx].encode()), len(text[:close_idx].encode())
        pf.structs.append(StructDef(
            name=name, is_component=is_comp,
            build_body_start_byte=b_start, build_body_end_byte=b_end,
            decl_start_byte=len(text[:decl_start].encode()),
            decl_end_byte=len(text[:body_end].encode()),
            start_point=(text[:decl_start].count("\n"), 0),
            end_point=(text[:body_end].count("\n"), 0)))


def _match_brace(text: str, open_idx: int) -> int:
    depth = 0
    i = open_idx
    while i < len(text):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return len(text)


def parse_file(path: str) -> ParsedFile:
    with open(path, "r", encoding="utf-8") as fh:
        source = fh.read()
    pf = parse_source(source, path=path)
    return pf
