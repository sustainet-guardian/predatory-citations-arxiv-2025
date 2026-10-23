# numbers/

One `<name>.numbers.json` per script that writes numbers (written by `stats_common.write_numbers()`): aggregates only, no
per-citation data. Each key holds a `kind` (`int`, `float` or `ratio`) with its value (`value`, or `num`, `den` and `decimals`
for a ratio). `write_numbers()` also writes the two fields `_source` (the script name) and `_generated_at` (a time stamp); the
released files do not contain them, only the keys with their kind and values, and `../make_numbers_tex.py` reads either form.
`../make_numbers_tex.py` collects all files into `numbers.tex`, one LaTeX macro per key.
