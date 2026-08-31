from __future__ import annotations
import argparse, sys, yaml
from pathlib import Path

CFG = Path("config/config.yaml")
EXAMPLE = Path("config/config.example.yaml")

def load_cfg():
    if CFG.exists():
        return yaml.safe_load(CFG.read_text(encoding="utf-8")) or {}
    if EXAMPLE.exists():
        return yaml.safe_load(EXAMPLE.read_text(encoding="utf-8")) or {}
    return {}

def save_cfg(d: dict):
    CFG.parent.mkdir(parents=True, exist_ok=True)
    CFG.write_text(yaml.safe_dump(d, sort_keys=False, allow_unicode=True), encoding="utf-8")
    print(f"Saved {CFG}")

def cmd_status(args):
    d = load_cfg()
    print(yaml.safe_dump(d, sort_keys=False, allow_unicode=True))
    # db check
    db = Path("data/bot.db")
    print(f"DB: {db} exists={db.exists()}")

def _ensure_list(d, k):
    if k not in d or d[k] is None:
        d[k]=[]
    if not isinstance(d[k], list):
        d[k]=[d[k]]

def cmd_add_chat(args):
    d=load_cfg(); _ensure_list(d,"allowed_chats")
    val=args.value
    # try int
    try: val_int=int(val); val=val_int
    except: pass
    if val not in d["allowed_chats"]:
        d["allowed_chats"].append(val)
        save_cfg(d); print(f"Added chat {val}")
    else:
        print("Already exists")

def cmd_add_whitelist(args):
    d=load_cfg(); _ensure_list(d,"whitelist_users")
    uid=int(args.user_id)
    if uid not in d["whitelist_users"]:
        d["whitelist_users"].append(uid); save_cfg(d); print(f"Added {uid}")
    else: print("Already exists")

def cmd_set_prompt(args):
    p=Path(args.path)
    if not p.exists():
        print(f"File not found: {p}", file=sys.stderr); sys.exit(1)
    d=load_cfg()
    d["filter_prompt_path"]=str(p)
    save_cfg(d)
    print(f"Prompt set to {p}. Перезапусти бота: sudo systemctl restart tg-antispam")

def cmd_set_model(args):
    d=load_cfg(); d["vega_model"]=args.model; save_cfg(d); print(f"Model -> {args.model}")

def main():
    ap=argparse.ArgumentParser(prog="tg-antispam", description="Управление ботом через терминал")
    sub=ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="Показать конфиг и статус")
    p2=sub.add_parser("add-chat", help="Добавить группу/канал"); p2.add_argument("value", help="ID (-100...) или @username"); p2.set_defaults(func=cmd_add_chat)
    p3=sub.add_parser("add-whitelist", help="Добавить пользователя в белый список"); p3.add_argument("user_id"); p3.set_defaults(func=cmd_add_whitelist)
    p4=sub.add_parser("set-prompt", help="Указать путь к filter_prompt.txt"); p4.add_argument("path"); p4.set_defaults(func=cmd_set_prompt)
    p5=sub.add_parser("set-model", help="Сменить модель api.vega.chat"); p5.add_argument("model", help="например gpt-4o-mini, claude-3.5-sonnet"); p5.set_defaults(func=cmd_set_model)
    # status default
    args=ap.parse_args()
    if args.cmd=="status":
        cmd_status(args)
    else:
        args.func(args)

if __name__=="__main__":
    main()
