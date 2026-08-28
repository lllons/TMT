# TMT

"To Many Tools" CLI coding agent.

## Quick start

Needs Python 3.8+.

```bash
git clone https://github.com/lllons/TMT.git
cd TMT
pip install requests rich      # optional: adds live streaming + colour
python agent1.py               # Windows: py agent1.py   macOS/Linux: python3 agent1.py
```

First launch asks for an [OpenRouter key](https://openrouter.ai/keys) and saves it to `.tmt_key` (git-ignored). Set `OPENROUTER_API_KEY` in your environment to skip that.

Type a task at the `Task>` prompt; `quit` to exit. The agent only touches the `output/` folder, created beside the code on first launch.

Run the tests with `python run_tests.py`.
