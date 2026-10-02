#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
奶昔论坛（forum.naixi.net）每日签到 —— 青龙面板版
====================================================

支持两种登录模式，优先级：Cookie > 账密（自动降级）

环境变量：
  NAIXI_ACCOUNT   推荐。账密，格式：账号1,密码1丨账号2,密码2
                  账号之间用 丨（竖线）分隔，账号与密码之间用 , 分隔。
                  例：yourname,YourPass123丨another,pwd456
  NAIXI_COOKIE    选填。浏览器 Cookie。多账号用 & 或换行分隔。
  NAIXI_UA        选填。User-Agent，建议与你抓 Cookie 时用的浏览器保持一致。
  NAIXI_WRITEBACK 选填。设 1 时，账密登录成功后把新 Cookie 写回青龙环境变量
                  NAIXI_COOKIE（多账号时按位置替换）。

  兼容旧格式：NAIXI_USER / NAIXI_PWD（多账号各自用 & 或换行分隔，一一对应）

运行：
  task naixi/naixi_checkin.py
cron 示例（每天 08:23 和 20:23 各跑一次，防漏）：
  23 8,20 * * *
"""

import os
import re
import sys
import time
import random
import traceback
import urllib.request
import urllib.error

try:
    import requests
except ImportError:
    os.system("pip3 install requests")
    import requests

BASE = "https://forum.naixi.net"
UA = os.environ.get(
    "NAIXI_UA",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
)
TIMEOUT = 30
RETRY = 3


def log(tag, msg):
    ts = time.strftime("%H:%M:%S")
    print("[%s] %s" % (ts, msg))


def notify(title, content):
    """青龙通知（优先走 QLAPI，失败则忽略）"""
    try:
        QLAPI  # noqa: F821
    except NameError:
        return
    try:
        QLAPI.systemNotify({"title": title, "content": content})
    except Exception:
        try:
            QLAPI.notify(title, content)
        except Exception:
            pass


def base_headers():
    return {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }


def make_session(cookie_str=""):
    s = requests.Session()
    h = base_headers()
    h["Referer"] = BASE + "/k_misign-sign.html"
    if cookie_str:
        h["Cookie"] = cookie_str.strip()
    s.headers.update(h)
    return s


def get_page(sess, url, **kw):
    for i in range(RETRY):
        try:
            r = sess.get(url, timeout=TIMEOUT, **kw)
            if r.status_code == 200:
                return r.text
            log("  ", "GET %s -> HTTP %s（重试 %d/%d）" % (url, r.status_code, i + 1, RETRY))
        except Exception as e:
            log("  ", "GET 异常: %s（重试 %d/%d）" % (e, i + 1, RETRY))
        time.sleep(2 + i * 2)
    return ""


# ---------------------------------------------------------------- 登录相关

def extract_formhash(html):
    m = (re.search(r'name="formhash"\s+value="([0-9a-zA-Z]+)"', html)
         or re.search(r'formhash=([0-9a-zA-Z]{6,10})', html)
         or re.search(r'formhash["\'\s:=]+([0-9a-zA-Z]{6,10})', html))
    return m.group(1) if m else ""


def extract_loginhash(html):
    """loginhash 在 form action 里：...&loginhash=XXXX'"""
    m = re.search(r'loginhash=([0-9a-zA-Z]+)', html)
    return m.group(1) if m else ""


def is_logged_in(html):
    """页面是否处于已登录态"""
    if not html:
        return False
    logout_marks = ("action=logout", "logging&action=logout", "退出登录")
    return any(k in html for k in logout_marks)


def do_login(sess, username, password):
    """账密登录。返回 (ok, cookie_str, msg)

    Discuz 登录流程：
      1. GET  login 页 → 取 formhash + loginhash
      2. POST login.php?...&loginsubmit=yes&loginhash=XXX
         表单：formhash / referer / username / password / questionid=0 / answer=
      3. 校验响应 + 回访首页确认登录态
    """
    page = get_page(sess, BASE + "/member.php?mod=logging&action=login")
    if not page:
        return False, "", "登录页访问失败"

    formhash = extract_formhash(page)
    loginhash = extract_loginhash(page)
    if not formhash or not loginhash:
        return False, "", "登录页缺少 formhash/loginhash"
    log("  ", "登录 formhash=%s loginhash=%s" % (formhash, loginhash))

    data = {
        "formhash": formhash,
        "referer": BASE + "/",
        "username": username,
        "password": password,
        "questionid": "0",
        "answer": "",
    }
    url = (BASE + "/member.php?mod=logging&action=login&loginsubmit=yes"
           "&loginhash=" + loginhash + "&inajax=1")
    try:
        r = sess.post(url, data=data, timeout=TIMEOUT,
                      headers={"Referer": BASE + "/member.php?mod=logging&action=login"})
        body = r.text or ""
    except Exception as e:
        return False, "", "登录请求异常: %s" % e

    # 常见失败文案
    for bad, msg in (
        ("密码错误", "密码错误"),
        ("登录失败", "登录失败（用户名或密码错）"),
        ("抱歉，您填写的账号错误", "账号不存在"),
        ("请填写验证码", "触发验证码，账密模式不可用"),
        ("安全提问", "需要安全提问答案"),
        ("两次登录间隔", "登录过于频繁，稍后重试"),
    ):
        if bad in body:
            return False, "", msg

    # 回访首页校验登录态（最可靠）
    time.sleep(random.uniform(1, 2))
    home = get_page(sess, BASE + "/forum.php")
    if not is_logged_in(home):
        snippet = re.sub(r"\s+", " ", body)[:200]
        return False, "", "登录未生效（%s）" % (snippet or "无响应")

    # 拼 Cookie 串
    ck = "; ".join("%s=%s" % (k, v) for k, v in sess.cookies.get_dict().items())
    return True, ck, "登录成功"


def _ql_api(method, path, data=None):
    """直连青龙 HTTP API（QLAPI 只暴露 notify，读写环境变量得走 HTTP）"""
    import json as _json

    base = "http://127.0.0.1:5700"
    # token 位置：青龙容器内
    tok = ""
    for p in ("/ql/data/config/auth.json", "/ql/config/auth.json"):
        if os.path.exists(p):
            try:
                tok = _json.load(open(p)).get("token", "")
            except Exception:
                pass
            if tok:
                break
    if not tok:
        raise RuntimeError("未找到青龙 auth.json")

    body = _json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(base + path, data=body, method=method)
    req.add_header("Authorization", "Bearer " + tok)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=20) as r:
        return _json.loads(r.read().decode())


def writeback_cookie(cookie_str, idx=1):
    """把新 Cookie 写回青龙环境变量 NAIXI_COOKIE（尽力而为）

    多账号时 NAIXI_COOKIE 是 & 分隔的整串，这里只替换第 idx 个（1-based）。

    注意：青龙的 PUT /api/envs 对 payload 有严格校验，
    只接受 {id, name, value}，多带 status/position/timestamp 等一律 400。
    """
    if os.environ.get("NAIXI_WRITEBACK", "") not in ("1", "true", "True"):
        return
    if not os.path.exists("/ql/data/config/auth.json") and \
       not os.path.exists("/ql/config/auth.json"):
        log("  ", "非青龙环境，跳过 Cookie 回写")
        return
    try:
        d = _ql_api("GET", "/api/envs?searchValue=NAIXI_COOKIE")
        rows = d.get("data", [])
        if not rows:
            log("  ", "未找到 NAIXI_COOKIE 环境变量，跳过回写")
            return
        rec = rows[0]
        eid = rec.get("id") or rec.get("_id")

        old = rec.get("value", "") or ""
        parts = split_multi(old)

        # 替换第 idx 个；若不足则补齐
        while len(parts) < idx:
            parts.append("")
        parts[idx - 1] = cookie_str
        new_val = "&".join(p for p in parts if p)

        # 关键：只传这三个字段
        _ql_api("PUT", "/api/envs",
                {"id": eid, "name": "NAIXI_COOKIE", "value": new_val})
        if len(parts) > 1:
            log("  ", "✅ 账号%d 的新 Cookie 已写回（共 %d 个账号，%d 字符）"
                % (idx, len(parts), len(cookie_str)))
        else:
            log("  ", "✅ 新 Cookie 已写回青龙环境变量（%d 字符）" % len(cookie_str))
    except Exception as e:
        log("  ", "Cookie 回写失败: %s" % e)


# ---------------------------------------------------------------- 签到相关

def is_logged_out(html):
    marks = ("您需要先登录", "请先登录", "member.php?mod=logging&action=login\"" )
    return any(k in html for k in marks)


def not_signed_yet(html):
    return "还没有签到" in html or "您今天还没有签到" in html


def already_signed(html):
    """k_misign 签到成功后：
         - 按钮 <a id="JD_sign"> → <span class="btn btnvisted">
         - 文案「您今天还没有签到」→「您的签到排名：N」
    """
    marks = (
        "btnvisted",
        "您的签到排名",
        "已经签到",
        "今日已签到",
        "您今天已经签到",
        "已连续签到",
    )
    if any(k in html for k in marks):
        return True
    if not_signed_yet(html):
        return False
    return False


def parse_stats(html):
    """抓统计字段，返回 dict（缺的不出现）

    k_misign 表单域：lxdays(连续) lxlevel(等级) lxtdays(累计)
                     lxreward(奖励) qiandaobtnnum(排名)
    """
    fields = {
        "lxdays": "con",
        "lxlevel": "level",
        "lxtdays": "total",
        "lxreward": "reward",
        "qiandaobtnnum": "rank",
    }
    out = {}
    for fid, key in fields.items():
        m = re.search(r'id="%s"\s+value="([^"]*)"' % fid, html)
        if m and m.group(1):
            out[key] = m.group(1)
    return out


def fmt_stats(stats):
    """把统计 dict 排成多行好看的样式"""
    if not stats:
        return ""
    lines = []
    if "con" in stats or "level" in stats:
        lines.append("📊 连续签到 %s 天 · 等级 Lv.%s"
                     % (stats.get("con", "?"), stats.get("level", "?")))
    tail = []
    if "total" in stats:
        tail.append("累计 %s 天" % stats["total"])
    if "reward" in stats:
        tail.append("今日奖励 +%s" % stats["reward"])
    if tail:
        lines.append("📅 " + " · ".join(tail))
    if "rank" in stats:
        lines.append("🏆 今日排名 第 %s 名" % stats["rank"])
    return "\n".join(lines)


HINTS = {
    "Cookie 失效": "Cookie 已失效，且未配置账密（NAIXI_ACCOUNT）。建议补上账密实现自动续期",
    "账密登录失败": "请检查 NAIXI_ACCOUNT 中的账号密码是否正确",
    "签到页访问失败": "设备网络异常，或站点前端防护拦住了请求",
    "签到未生效": "插件接口可能变更，请到签到页手动确认",
}


def try_sign(sess, formhash):
    """依次尝试 k_misign 的几种提交方式"""
    attempts = [
        # 2026-10-02 实测：本站 k_misign 签到按钮 href 为 operation=qiandao&format=empty
        ("GET+qiandao", BASE + "/plugin.php?id=k_misign:sign&operation=qiandao&formhash=%s&format=empty" % formhash, None),
        ("GET+qiandao+inajax", BASE + "/plugin.php?id=k_misign:sign&operation=qiandao&formhash=%s&format=empty&inajax=1" % formhash, None),
        ("GET+ajax", BASE + "/plugin.php?id=k_misign:sign&operation=sign&formhash=%s&inajax=1" % formhash, None),
        ("GET", BASE + "/plugin.php?id=k_misign:sign&operation=sign&formhash=%s" % formhash, None),
        ("POST", BASE + "/plugin.php?id=k_misign:sign&operation=sign", {"formhash": formhash}),
        ("POST2", BASE + "/plugin.php?id=k_misign:sign", {"formhash": formhash, "operation": "sign"}),
    ]
    results = []
    for name, url, data in attempts:
        try:
            if data is None:
                r = sess.get(url, timeout=TIMEOUT)
            else:
                r = sess.post(url, data=data, timeout=TIMEOUT)
            body = r.text or ""
            results.append((name, r.status_code, body))
            if any(k in body for k in ("签到成功", "已经签到", "今日已签到", "已连续", "success")):
                return name, body
            if "formhash" in body.lower() and "error" in body.lower():
                continue
        except Exception as e:
            results.append((name, "EXC", str(e)))
        time.sleep(random.uniform(1.5, 3))
    name, st, body = results[-1] if results else ("none", 0, "")
    return None, body


def do_checkin(idx, sess, label):
    """已建立会话后执行签到。返回 (ok, stats_dict, reason)"""
    html = get_page(sess, BASE + "/k_misign-sign.html")
    if not html:
        log("账号%d" % idx, "❌ 签到页访问失败")
        return False, None, "签到页访问失败"

    if is_logged_out(html) or not is_logged_in(html):
        return False, None, "NOT_LOGGED_IN"

    stats = parse_stats(html)

    if already_signed(html):
        log("账号%d" % idx, "✅ 今日已签到  连续%s 等级%s 排名%s"
            % (stats.get("con", "?"), stats.get("level", "?"), stats.get("rank", "?")))
        return True, stats, "已签到"

    formhash = extract_formhash(html)
    if not formhash:
        log("账号%d" % idx, "❌ 页面未找到 formhash")
        return False, stats, "页面无 formhash"
    log("账号%d" % idx, "formhash=%s，执行签到…" % formhash)

    method, body = try_sign(sess, formhash)

    time.sleep(random.uniform(2, 3.5))
    html2 = get_page(sess, BASE + "/k_misign-sign.html")
    if bool(html2) and already_signed(html2):
        stats = parse_stats(html2) or stats
        log("账号%d" % idx, "✅ 签到成功（%s）  连续%s 等级%s 排名%s"
            % (method or "已生效", stats.get("con", "?"),
               stats.get("level", "?"), stats.get("rank", "?")))
        return True, stats, "签到成功"

    if html2 and not_signed_yet(html2):
        log("账号%d" % idx, "❌ 签到未生效，响应: %s"
            % re.sub(r"\s+", " ", (body or ""))[:160])
        return False, stats, "签到未生效"

    snippet = re.sub(r"\s+", " ", (body or ""))[:160] or "无响应"
    log("账号%d" % idx, "⚠️ 签到状态未知: %s" % snippet)
    return False, stats, "签到状态未知"


def process_account(idx, cookie="", user="", pwd=""):
    """单账号完整流程：建会话 → 校验 → （必要时）账密登录 → 签到"""
    log("账号%d" % idx, "开始处理（模式：%s）"
        % ("Cookie" if cookie else ("账密" if user else "无凭据")))

    sess = make_session(cookie)
    new_cookie = ""

    # 1) 有 Cookie 先用 Cookie
    if cookie:
        ok, stats, reason = do_checkin(idx, sess, "cookie")
        if ok:
            return True, stats, reason, ""
        if reason == "签到页访问失败":
            return False, stats, reason, ""
        if reason != "NOT_LOGGED_IN":
            return False, stats, reason, ""
        log("账号%d" % idx, "Cookie 失效，尝试账密登录…")
    else:
        stats = None

    # 2) 账密登录
    if not user or not pwd:
        return False, stats, "Cookie 失效", ""

    sess = make_session()          # 干净会话
    ok, new_cookie, msg = do_login(sess, user, pwd)
    if not ok:
        log("账号%d" % idx, "❌ 账密登录失败：%s" % msg)
        return False, stats, "账密登录失败", ""

    log("账号%d" % idx, "✅ %s，继续签到…" % msg)
    ok2, stats2, reason = do_checkin(idx, sess, "login")
    return ok2, stats2, reason, new_cookie


def split_multi(raw):
    """按换行或 & 分隔"""
    return [x.strip() for x in re.split(r"[\n&]+", raw) if x.strip()]


def parse_accounts(raw_account):
    """解析 NAIXI_ACCOUNT：账号1,密码1丨账号2,密码2

    - 账号之间用 丨 (U+4E28) 或 | 分隔
    - 账号与密码之间用 , （半角）或 ，（全角）分隔
    - 允许「只有账号没有密码」（此时该账号只能靠 Cookie）
    返回 [(user, pwd), ...]
    """
    out = []
    for chunk in re.split(r"[丨|]", raw_account):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = re.split(r"[,，]", chunk, 1)
        user = parts[0].strip()
        pwd = parts[1].strip() if len(parts) > 1 else ""
        if user:
            out.append((user, pwd))
    return out


def build_accounts():
    """组装账号池。返回 [(cookie, user, pwd), ...]

    凭据来源（可混用）：
      NAIXI_ACCOUNT   账号1,密码1丨账号2,密码2   ← 推荐
      NAIXI_COOKIE    Cookie，多账号用 & 或换行分隔
      NAIXI_USER / NAIXI_PWD   旧格式，向后兼容
    """
    raw_account = os.environ.get("NAIXI_ACCOUNT", "").strip()
    raw_cookie = os.environ.get("NAIXI_COOKIE", "").strip()
    raw_user = os.environ.get("NAIXI_USER", "").strip()
    raw_pwd = os.environ.get("NAIXI_PWD", "").strip()

    pairs = parse_accounts(raw_account)

    # 兼容旧格式：NAIXI_USER / NAIXI_PWD 按位置合并进 pairs
    if not pairs:
        users = split_multi(raw_user)
        pwds = split_multi(raw_pwd)
        for i in range(max(len(users), len(pwds))):
            u = users[i] if i < len(users) else ""
            p = pwds[i] if i < len(pwds) else ""
            if u or p:
                pairs.append((u, p))

    cookies = split_multi(raw_cookie)

    n = max(len(cookies), len(pairs))
    accounts = []
    for i in range(n):
        ck = cookies[i] if i < len(cookies) else ""
        u, p = pairs[i] if i < len(pairs) else ("", "")
        if ck or u or p:
            accounts.append((ck, u, p))
    return accounts


def main():
    accounts = build_accounts()

    if not accounts:
        print("❌ 未配置凭据")
        print("   推荐：NAIXI_ACCOUNT = 账号1,密码1丨账号2,密码2")
        print("   或  ：NAIXI_COOKIE（浏览器 Cookie，多账号用 & 或换行分隔）")
        sys.exit(1)

    has_login = any(u and p for _, u, p in accounts)
    log("系统", "共 %d 个账号（%s）"
        % (len(accounts), "Cookie + 账密" if has_login else "仅 Cookie"))
    results = []   # (i, ok, stats, reason)
    for i, (ck, u, p) in enumerate(accounts, 1):
        try:
            ok, stats, reason, new_ck = process_account(i, ck, u, p)
        except Exception as e:
            log("账号%d" % i, "❌ 异常: %s" % e)
            log("TRACE", traceback.format_exc(limit=2))
            ok, stats, reason, new_ck = False, None, "运行异常", ""
        results.append((i, ok, stats, reason))
        if new_ck:
            writeback_cookie(new_ck, i)
        if i < len(accounts):
            time.sleep(random.uniform(3, 6))

    # ---------- 控制台简报 ----------
    print("\n" + "=" * 42)
    for i, ok, stats, reason in results:
        print("%s 账号%d：%s" % ("✅" if ok else "❌", i, reason))
    print("=" * 42)

    # ---------- 通知排版 ----------
    n = len(results)
    n_ok = sum(1 for r in results if r[1])
    if n_ok == n:
        title = "✅ 奶昔论坛签到成功"
    elif n_ok == 0:
        title = "❌ 奶昔论坛签到失败"
    else:
        title = "⚠️ 奶昔论坛签到 %d/%d 成功" % (n_ok, n)

    body = []
    for i, ok, stats, reason in results:
        body.append("👤 账号%d · %s" % (i, reason))
        if ok:
            fs = fmt_stats(stats)
            if fs:
                body.append(fs)
        else:
            hint = HINTS.get(reason)
            if hint:
                body.append("💬 " + hint)
        body.append("")
    while body and not body[-1]:
        body.pop()
    body.append("🕐 " + time.strftime("%Y-%m-%d %H:%M:%S"))

    notify(title, "\n".join(body))
    sys.exit(0 if n_ok == n else 1)


if __name__ == "__main__":
    main()
