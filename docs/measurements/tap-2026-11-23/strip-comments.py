"""Make run B's project: the same files with every comment removed.

usage: python3 -I strip-comments.py <copy of app/, cypress/ and cypress.config.js>
Edits the copy in place: drops the <!-- --> block and the // lines of
app/index.html, and the // lines of the spec and the config. The code is
not changed.
"""
import re
import sys

root = sys.argv[1]


def drop_line_comments(text):
    return "\n".join(line for line in text.split("\n") if not line.strip().startswith("//"))


page = f"{root}/app/index.html"
with open(page) as f:
    text = f.read()
text = drop_line_comments(re.sub(r"<!--.*?-->\n", "", text, flags=re.S))
with open(page, "w") as f:
    f.write(text)

for path in (f"{root}/cypress/e2e/orders.cy.js", f"{root}/cypress.config.js"):
    with open(path) as f:
        text = f.read()
    with open(path, "w") as f:
        f.write(drop_line_comments(text).lstrip("\n"))
