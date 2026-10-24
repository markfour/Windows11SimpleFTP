"""SimpleFTP - FTP処理部（UIに依存しない）"""
from __future__ import annotations

import ftplib
import os
import posixpath
import ssl
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

CHUNK = 64 * 1024


@dataclass
class Entry:
    name: str
    is_dir: bool
    size: int = 0
    modified: Optional[datetime] = None

    @property
    def size_text(self) -> str:
        return "" if self.is_dir else human_size(self.size)

    @property
    def modified_text(self) -> str:
        return self.modified.strftime("%Y/%m/%d %H:%M") if self.modified else ""


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def parse_list_line(line: str) -> Optional[Entry]:
    """UNIX形式 / Windows(IIS)形式の LIST 出力を1行解析する"""
    line = line.rstrip("\r\n")
    if not line or line.startswith("total "):
        return None
    # Windows (IIS): 10-21-26  09:15AM  <DIR>  name  /  10-21-26  09:15AM  1234 name
    parts = line.split(None, 3)
    if len(parts) == 4 and len(parts[0]) in (8, 10) and parts[0][2] == "-":
        try:
            fmt = "%m-%d-%y" if len(parts[0]) == 8 else "%m-%d-%Y"
            dt = datetime.strptime(parts[0] + " " + parts[1], fmt + " %I:%M%p")
        except ValueError:
            dt = None
        if parts[2].upper() == "<DIR>":
            return Entry(parts[3], True, 0, dt)
        try:
            return Entry(parts[3], False, int(parts[2]), dt)
        except ValueError:
            return None
    # UNIX: drwxr-xr-x 2 user group 4096 Oct 21 09:15 name
    parts = line.split(None, 8)
    if len(parts) < 9:
        return None
    perms, size, mon, day, t, name = parts[0], parts[4], parts[5], parts[6], parts[7], parts[8]
    if perms.startswith("l") and " -> " in name:
        name = name.split(" -> ", 1)[0]
    dt = None
    try:
        m = _MONTHS[mon.lower()[:3]]
        if ":" in t:
            hh, mm = t.split(":")
            now = datetime.now()
            dt = datetime(now.year, m, int(day), int(hh), int(mm))
            if dt > now:
                dt = dt.replace(year=now.year - 1)
        else:
            dt = datetime(int(t), m, int(day))
    except (KeyError, ValueError):
        pass
    try:
        sz = int(size)
    except ValueError:
        sz = 0
    return Entry(name, perms.startswith(("d", "l")), sz, dt)


def list_local(path: str) -> list[Entry]:
    out = []
    with os.scandir(path) as it:
        for e in it:
            try:
                st = e.stat()
                out.append(Entry(e.name, e.is_dir(), st.st_size,
                                 datetime.fromtimestamp(st.st_mtime)))
            except OSError:
                continue
    return sort_entries(out)


def sort_entries(entries: list[Entry]) -> list[Entry]:
    return sorted(entries, key=lambda e: (not e.is_dir, e.name.lower()))


class Cancelled(Exception):
    pass


Progress = Callable[[str, int, int], None]  # (ファイル名, 転送済み, 合計)


class FtpClient:
    def __init__(self):
        self.ftp: Optional[ftplib.FTP] = None
        self.cancel_flag = False
        self.use_mlsd = True
        self._params: tuple = ()

    # ---------- 接続 ----------
    @property
    def connected(self) -> bool:
        return self.ftp is not None

    def connect(self, host: str, port: int, user: str, password: str,
                tls: bool = False, passive: bool = True, encoding: str = "utf-8",
                timeout: int = 20, verify: bool = True) -> str:
        self.close()
        self._params = (host, port, user, password, tls, passive, encoding, timeout, verify)
        if tls:
            ctx = ssl.create_default_context()
            if not verify:
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            ftp: ftplib.FTP = ftplib.FTP_TLS(context=ctx, timeout=timeout, encoding=encoding)
        else:
            ftp = ftplib.FTP(timeout=timeout, encoding=encoding)
        try:
            welcome = ftp.connect(host, port)
            if tls:
                ftp.auth()
            ftp.login(user or "anonymous", password or "")
            if tls:
                ftp.prot_p()
            ftp.set_pasv(passive)
            if encoding.lower().replace("-", "") == "utf8":
                try:
                    ftp.sendcmd("OPTS UTF8 ON")
                except ftplib.Error:
                    pass
        except BaseException:
            ftp.close()
            raise
        self.ftp = ftp
        self.use_mlsd = True
        return welcome

    def reconnect(self, cwd: Optional[str] = None):
        """キャンセル後など、制御接続の状態が不定になったときに張り直す"""
        if self.ftp:
            try:
                self.ftp.close()   # QUIT は送らず即切断（応答待ちで固まらないように）
            except Exception:
                pass
            self.ftp = None
        self.connect(*self._params)
        if cwd:
            try:
                self._f().cwd(cwd)
            except ftplib.Error:
                pass

    def close(self):
        if self.ftp:
            try:
                self.ftp.quit()
            except Exception:
                try:
                    self.ftp.close()
                except Exception:
                    pass
        self.ftp = None

    def _f(self) -> ftplib.FTP:
        if not self.ftp:
            raise ConnectionError("接続されていません")
        return self.ftp

    # ---------- 参照 ----------
    def pwd(self) -> str:
        return self._f().pwd()

    def listdir(self, path: str) -> list[Entry]:
        ftp = self._f()
        entries: list[Entry] = []
        if self.use_mlsd:
            try:
                for name, facts in ftp.mlsd(path, facts=["type", "size", "modify"]):
                    t = facts.get("type", "file").lower()
                    if t in ("cdir", "pdir") or name in (".", ".."):
                        continue
                    mod = None
                    if "modify" in facts:
                        try:
                            mod = datetime.strptime(facts["modify"][:14], "%Y%m%d%H%M%S")
                        except ValueError:
                            pass
                    entries.append(Entry(name, t == "dir" or t.startswith("os.unix=slink"),
                                         int(facts.get("size", 0) or 0), mod))
                return sort_entries(entries)
            except ftplib.error_perm as e:
                if not str(e).startswith(("500", "501", "502", "504")):
                    raise
                self.use_mlsd = False
        lines: list[str] = []
        ftp.retrlines("LIST " + path if path else "LIST", lines.append)
        for ln in lines:
            ent = parse_list_line(ln)
            if ent and ent.name not in (".", ".."):
                entries.append(ent)
        return sort_entries(entries)

    # ---------- 操作 ----------
    def mkdir(self, path: str):
        self._f().mkd(path)

    def rename(self, src: str, dst: str):
        self._f().rename(src, dst)

    def delete(self, path: str, is_dir: bool):
        ftp = self._f()
        if not is_dir:
            ftp.delete(path)
            return
        for e in self.listdir(path):
            self._check_cancel()
            self.delete(posixpath.join(path, e.name), e.is_dir)
        ftp.rmd(path)

    # ---------- 転送 ----------
    def _check_cancel(self):
        if self.cancel_flag:
            raise Cancelled()

    def upload(self, local: str, remote: str, progress: Optional[Progress] = None):
        if os.path.isdir(local):
            try:
                self._f().mkd(remote)
            except ftplib.error_perm:
                pass  # 既存
            for name in sorted(os.listdir(local)):
                self._check_cancel()
                self.upload(os.path.join(local, name), posixpath.join(remote, name), progress)
            return
        total = os.path.getsize(local)
        done = 0
        name = os.path.basename(local)

        def cb(block):
            nonlocal done
            done += len(block)
            if progress:
                progress(name, done, total)
            self._check_cancel()

        with open(local, "rb") as fp:
            if progress:
                progress(name, 0, total)
            self._f().storbinary("STOR " + remote, fp, CHUNK, cb)

    def download(self, remote: str, local: str, is_dir: bool,
                 progress: Optional[Progress] = None, size: int = 0):
        if is_dir:
            os.makedirs(local, exist_ok=True)
            for e in self.listdir(remote):
                self._check_cancel()
                self.download(posixpath.join(remote, e.name), os.path.join(local, e.name),
                              e.is_dir, progress, e.size)
            return
        name = posixpath.basename(remote)
        done = 0
        tmp = local + ".part"
        try:
            with open(tmp, "wb") as fp:
                def cb(block):
                    nonlocal done
                    fp.write(block)
                    done += len(block)
                    if progress:
                        progress(name, done, size)
                    self._check_cancel()
                if progress:
                    progress(name, 0, size)
                self._f().retrbinary("RETR " + remote, cb, CHUNK)
            os.replace(tmp, local)
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
