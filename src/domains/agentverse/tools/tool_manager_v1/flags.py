import re

_FLAG_RE = re.compile(
    r"""
    (?:^|\s)                  # start or whitespace
    --(?P<key>[A-Za-z0-9_\-]+) # --flag-name
    (?:                       # optional =value part
    =(?P<val>
        "(?:[^"\\]|\\.)*"     # "quoted"
        | '(?:[^'\\]|\\.)*'   # 'quoted'
        | [^\s]+              # bare
    )
    )?
    """,
    re.VERBOSE,
)
