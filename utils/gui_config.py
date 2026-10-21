"""GUI 配置读写：仅替换选定赋值，保留其余配置、表达式和注释。"""
import ast
import os
import runpy
import tempfile
from pathlib import Path


def load_config(path: Path) -> dict:
    return runpy.run_path(str(path))


def save_config(path: Path, values: dict) -> None:
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines(keepends=True)
    replacements = {}
    for node in ast.parse(source).body:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        for target in targets:
            if isinstance(target, ast.Name) and target.id in values:
                replacements[target.id] = (node.lineno - 1, node.end_lineno)
    missing = values.keys() - replacements.keys()
    if missing:
        raise ValueError(f"配置项不存在：{', '.join(sorted(missing))}")
    for key, (start, end) in sorted(replacements.items(), key=lambda item: item[1][0], reverse=True):
        lines[start:end] = [f"{key} = {values[key]!r}\n"]
    result = "".join(lines)
    ast.parse(result)
    # 同目录原子替换，避免保存中断时只写入半个配置。
    fd, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(result)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
