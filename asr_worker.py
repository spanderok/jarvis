# /// script
# requires-python = ">=3.10,<3.13"
# dependencies = ["parakeet-mlx"]
# ///
"""Long-lived speech-to-text worker: keeps parakeet loaded between utterances.

Protocol (line based, UTF-8):
  stdin   "<path to wav>"   transcribe this file
          "!QUIT"           exit
  stdout  "!READY"          model loaded, send work
          "TEXT <one line>" the transcript (empty line means nothing recognised)
          "!ERR <msg>"      this file failed

Why a worker: `uvx parakeet-mlx <file>` reloaded the 600 MB model every time,
which cost 1.5-2.5 s per question.
"""
import sys

from parakeet_mlx import from_pretrained

MODEL = "mlx-community/parakeet-tdt-0.6b-v3"


def emit(msg: str) -> None:
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


model = from_pretrained(MODEL)
emit("!READY")

for line in sys.stdin:
    path = line.strip()
    if not path or path == "!QUIT":
        break
    try:
        result = model.transcribe(path)
        text = " ".join(getattr(result, "text", "").split())
        emit(f"TEXT {text}")
    except Exception as e:  # one bad file must not kill the worker
        emit(f"!ERR {e}")
