from pathlib import Path

# Pasta raiz do projeto
project_dir = Path(r"C:\Users\JULIO\Desktop\site\ALFA TASK\V1")
output_file = project_dir / "site.md"

# Extensões aceitas
file_types = {
    ".html": "html",
    ".css": "css",
    ".js": "javascript",
    ".py": "python",   # Inclui arquivos .py
}

markdown_output = []

# Busca recursiva em todas as pastas do projeto
for file_path in sorted(project_dir.rglob("*")):
    if (
        file_path.is_file()
        and file_path.suffix in file_types
        and not file_path.name.endswith(".bat")     # Ignora arquivos .bat
        and file_path.name != "gerar_prompt.py"     # Ignora o próprio script
        and "venv" not in file_path.parts           # Ignora venv (opcional, mas recomendado)
    ):
        lang = file_types[file_path.suffix]
        code = file_path.read_text(encoding="utf-8", errors="ignore")
        relative_path = file_path.relative_to(project_dir)
        section = f"## {relative_path}\n```{lang}\n{code}\n```\n"
        markdown_output.append(section)

# Salva o resultado em site.md
output_file.write_text("\n\n".join(markdown_output), encoding="utf-8")
print(f">>> Prompt gerado com sucesso: {output_file}")
