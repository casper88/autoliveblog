"""Windows 桌面通知(選用,失敗時靜默略過)。"""


def toast(title: str, message: str) -> None:
    try:
        from winotify import Notification
        n = Notification(app_id="autoliveblog", title=title,
                         msg=message[:200], duration="short")
        n.show()
    except Exception:
        pass
