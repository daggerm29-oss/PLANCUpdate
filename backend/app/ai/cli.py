"""AI CLI. Run from backend/:
  python -m app.ai.cli login     # browser OAuth via your auth.perform_login(); tokens -> CODEX_TOKEN_FILE
  python -m app.ai.cli status    # provider + budget status (no model request)
  python -m app.ai.cli models    # your fetch_available_model() (model discovery, no completion)
  python -m app.ai.cli ping      # exactly ONE budgeted model request through the central manager
"""
from __future__ import annotations

import argparse
import json

from .manager import AIUnavailable, get_manager
from .providers import CodexProvider, ProviderError


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="python -m app.ai.cli")
    p.add_argument("cmd", choices=["login", "status", "models", "select-model", "disconnect", "ping"])
    p.add_argument("--model", help="Model name for select-model", default=None)
    args = p.parse_args(argv)
    manager = get_manager()
    provider = manager.provider
    try:
        if args.cmd == "status":
            st = manager.status()
            st.pop("recent_requests", None)
            print(json.dumps(st, indent=2, default=str))
            return
        if not isinstance(provider, CodexProvider):
            raise SystemExit(f"AI_PROVIDER={provider.name}; set AI_PROVIDER=codex in backend/.env for {args.cmd}")
        if args.cmd == "login":
            missing = provider.missing_config()
            if missing:
                raise SystemExit("missing in backend/.env: " + ", ".join(missing))
            provider.login()
            print(f"[+] logged in; tokens saved to auth_session.json (gitignored)")
        elif args.cmd == "models":
            models = provider.discover_models()
            print(json.dumps({"selected": provider.model, "available_models": models}, indent=2))
        elif args.cmd == "select-model":
            if not args.model:
                raise SystemExit("--model is required for select-model")
            ok = provider.set_model(args.model)
            if ok:
                print(f"[+] selected model updated to: {args.model}")
            else:
                raise SystemExit("failed to update model selection (not logged in)")
        elif args.cmd == "disconnect":
            provider.disconnect()
            print("[+] logged out and session cleared")
        elif args.cmd == "ping":
            res = manager.complete("ping", "Reply with the single word: pong", "ping", use_cache=False)
            print(json.dumps({"text": res.text[:200], "model": res.model, "usage": res.usage}, default=str))
    except ProviderError as exc:
        raise SystemExit(f"[{exc.kind}] {exc}")
    except AIUnavailable as exc:
        raise SystemExit(f"[{exc.reason}] {exc} (retry_after={exc.retry_after})")


if __name__ == "__main__":
    main()
