import asyncio

from test_priorities import JARVIS, ME, toolbox


def test_peek(tmp_path):
    tools, _ = toolbox(tmp_path, notes={"Projects/Me.md": ME, "Projects/J.A.R.V.I.S.md": JARVIS},
                       refuse=True)
    print(asyncio.run(tools.run("daily_priorities", {})))
