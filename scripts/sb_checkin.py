#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
烧饼论坛（sb.sb）每日签到脚本 —— Cookie 版 v2，纯标准库零依赖
=============================================================
原理：登录时有 Cap.js 人机验证（PoW+浏览器探针），无法纯脚本登录；
     但签到本身不需要验证 -> 用浏览器 Cookie 直接签到。

v2 新增：Cookie 滑动续期自动写回。
     服务端若在响应中重发 __Host-bbs_* Cookie（滑动续期），
     脚本会把新值合并并写回 SB_COOKIE_FILE，实现长期免维护。

环境变量：
  SB_COOKIE       浏览器完整 Cookie（与 SB_COOKIE_FILE 二选一），形如：
                  __Host-bbs_csrf=xxx; __Host-bbs_session=yyy
  SB_COOKIE_FILE  Cookie 文件路径（推荐）。脚本从该文件读取 Cookie，
                  并把服务端续期后的新 Cookie 自动写回该文件。
  SB_MESSAGE      选填。签到留言（最多 100 字）
  SB_UA           选填。User-Agent（默认模拟 Android Chrome）

失效表现：脚本输出「Cookie 失效」，需从浏览器重新复制到 Cookie 文件。
"""
import os
import re
import sys
import time
import urllib.request
import urllib.error
import urllib.parse

BASE = "https://sb.sb"
UA = os.environ.get(
    "SB_UA",
    "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/137.0.0.0 Mobile Safari/537.36",
)

# 可选：强制走 IPv4 出口（SB_FORCE_IPV4=1，绕开被封的 IPv6 出口）
if os.environ.get("SB_FORCE_IPV4") == "1":
    import socket as _socket

    _orig_getaddrinfo = _socket.getaddrinfo

    def _getaddrinfo_inet4(*args, **kwargs):
        return [r for r in _orig_getaddrinfo(*args, **kwargs)
                if r[0] == _socket.AF_INET]

    _socket.getaddrinfo = _getaddrinfo_inet4
    print("[IPv4] 已强制使用 IPv4 出口", flush=True)


def log(msg):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def notify(title, content):
    """青龙通知（QLAPI；非青龙环境自动跳过）"""
    q = None
    try:
        q = QLAPI  # noqa: F821
    except NameError:
        return
    try:
        q.systemNotify({"title": title, "content": content})
        print("[QLAPI] 通知已提交")
    except Exception:
        try:
            q.notify(title, content)
            print("[QLAPI] 通知已提交(legacy)")
        except Exception:
            pass


# ---------- Cookie 手动管理（不用 CookieJar：保证每轮请求都带上完整
# Cookie，并把服务端 Set-Cookie 续期合并进下一轮请求） ----------

def parse_cookie(s):
    d = {}
    for part in (s or "").split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            k = k.strip()
            if k:
                d[k] = v.strip()
    return d


def apply_set_cookie(cookie_d, set_cookie_headers):
    """合并响应中的 Set-Cookie；空值视为删除该 Cookie"""
    for sc in set_cookie_headers or []:
        kv = sc.split(";", 1)[0]
        if "=" not in kv:
            continue
        k, v = kv.split("=", 1)
        k, v = k.strip(), v.strip()
        if not k:
            continue
        if v in ("", "deleted"):
            cookie_d.pop(k, None)
        else:
            cookie_d[k] = v


def cookie_header(cookie_d):
    return "; ".join("%s=%s" % (k, v) for k, v in cookie_d.items())


def cookie_file_path():
    """Cookie 文件路径：环境变量优先；青龙容器内用默认路径"""
    p = os.environ.get("SB_COOKIE_FILE", "").strip()
    if not p and os.path.isfile("/ql/data/config/sb_cookie"):
        p = "/ql/data/config/sb_cookie"  # 青龙挂载目录默认位置
    return p


def save_cookie_file(cookie_d):
    """把（可能被服务端续期过的）Cookie 写回 SB_COOKIE_FILE"""
    path = cookie_file_path()
    if not path:
        return
    data = cookie_header(cookie_d)
    try:
        if os.path.exists(path):
            with open(path) as f:
                if f.read().strip() == data:
                    return  # 无变化不写
        fd = os.open(path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(data)
        os.replace(path + ".tmp", path)
        log("💾 服务端续期了 Cookie，已写回 %s" % path)
    except Exception as e:
        log("⚠️ Cookie 写回失败: %s" % e)


def make_opener():
    op = urllib.request.build_opener()
    op.addheaders = [("User-Agent", UA), ("Accept-Language", "zh-CN,zh;q=0.9")]
    return op


def _open(op, req, cookie_d):
    """带 HTTP 错误兜底的请求：403/404 封禁页、5xx 都不抛异常，
    返回 (状态码, 最终URL, 响应体文本)"""
    try:
        with op.open(req, timeout=30) as r:
            apply_set_cookie(cookie_d, r.headers.get_all("Set-Cookie"))
            return r.status, r.geturl(), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            body = ""
        try:
            url = e.geturl()
        except Exception:
            url = ""
        try:
            apply_set_cookie(cookie_d, e.headers.get_all("Set-Cookie") if e.headers else None)
        except Exception:
            pass
        return e.code, url, body
    except urllib.error.URLError as e:
        return 0, "", "URLError: %s" % getattr(e, "reason", e)
    except Exception as e:
        return 0, "", "Error: %s" % e


def get(op, url, cookie_d):
    req = urllib.request.Request(url)
    for k, v in op.addheaders:
        req.add_header(k, v)
    req.add_header("Cookie", cookie_header(cookie_d))
    return _open(op, req, cookie_d)


def post(op, url, data, referer, cookie_d):
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("Referer", referer)
    req.add_header("Cookie", cookie_header(cookie_d))
    return _open(op, req, cookie_d)


def checkin():
    cookie = os.environ.get("SB_COOKIE", "").strip()
    cookie_file = cookie_file_path()
    if not cookie and cookie_file:
        try:
            with open(cookie_file) as f:
                cookie = f.read().strip()
        except Exception as e:
            log("⚠️ 读取 %s 失败: %s" % (cookie_file, e))
    message = os.environ.get("SB_MESSAGE", "").strip()[:100]
    if not cookie:
        print("❌ 未配置 SB_COOKIE / SB_COOKIE_FILE")
        return False, "未配置 Cookie"

    d = parse_cookie(cookie)
    op = make_opener()

    # 1) 打开签到页
    st, final_url, html = get(op, BASE + "/checkin/", d)

    if st == 0:
        log("❌ 网络请求失败: %s" % html[:150])
        return False, "网络错误: %s" % html[:80]

    if "/login" in final_url:
        save_cookie_file(d)  # 忠实保存当前状态（可能已被服务端清空）
        log("❌ Cookie 已失效（被重定向到登录页）")
        return False, "Cookie 失效"
    if "每日签到" not in html:
        log("❌ 页面异常，未找到签到面板 (HTTP %s)" % st)
        return False, "页面异常(HTTP %s)" % st

    # Cookie 可能刚被服务端滑动续期，立刻写回
    save_cookie_file(d)

    # 2) 判断今天是否已签（日历 today 是否带 done）
    today_done = re.search(r'signin-cal-day[^"]*\btoday\b[^"]*\bdone\b', html) or \
                 re.search(r'signin-cal-day[^"]*\bdone\b[^"]*\btoday\b', html)
    if today_done:
        log("✅ 今天已经签过了，无需重复")
        return True, "已签到 %s" % parse_stats(html)

    m = re.search(r'name="_csrf"\s+value="([^"]+)"', html)
    if not m:
        log("❌ 未找到 _csrf")
        return False, "页面无 _csrf"
    csrf = m.group(1)
    log("csrf=%s… 准备签到" % csrf[:12])

    # 3) 提交签到
    data = {"_csrf": csrf}
    if message:
        data["message"] = message
    st, final_url, resp = post(op, BASE + "/checkin/", data, BASE + "/checkin/", d)
    log("签到 POST -> HTTP %s" % st)

    # 4) 回访确认
    time.sleep(2)
    st2, url2, html2 = get(op, BASE + "/checkin/", d)
    save_cookie_file(d)
    if "/login" in url2:
        log("❌ 签到后会话失效（异常）")
        return False, "Cookie 失效"
    today_done2 = re.search(r'signin-cal-day[^"]*\btoday\b[^"]*\bdone\b', html2) or \
                  re.search(r'signin-cal-day[^"]*\bdone\b[^"]*\btoday\b', html2)
    stats = parse_stats(html2)
    if today_done2:
        log("✅ 签到成功！%s" % stats)
        return True, "签到成功 %s" % stats

    # 页面错误提示兜底
    err = re.search(r'error-message">([^<]+)', resp) or re.search(r'error-message">([^<]+)', html2)
    if err:
        log("❌ 签到失败: %s" % err.group(1).strip())
        return False, err.group(1).strip()

    if "已签到" in html2 or "签到成功" in html2 or "明天再来" in html2:
        log("✅ 签到成功（文案命中）%s" % stats)
        return True, "签到成功 %s" % stats

    log("⚠️ 结果未知，页面片段: %s" % re.sub(r"\s+", " ", html2)[:200])
    return False, "结果未知"


def parse_stats(html):
    vals = re.findall(r'signin-(?:streak-num|stat-value)">([^<]+)<', html)
    labels = re.findall(r'signin-(?:streak-label|stat-label)">([^<]+)<', html)
    if vals and labels and len(vals) == len(labels):
        return " ".join("%s=%s" % (l.strip(), v.strip()) for l, v in zip(labels, vals))[:4 * 20]
    return ""


def main():
    ok, reason = checkin()
    print("=" * 40)
    print("%s 烧饼论坛签到：%s" % ("✅" if ok else "❌", reason))
    print("=" * 40)
    notify(
        "%s 烧饼论坛签到" % ("✅" if ok else "❌"),
        "结果：%s\n时间：%s" % (reason, time.strftime("%Y-%m-%d %H:%M:%S")),
    )
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
