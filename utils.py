import random
import string

ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"


def random_name(prefix: str = "", suffix: str = "", letters: bool = True, digits: bool = True, min_len: int = 3, max_len: int = 8, name_mode: str = "random", counter: int | None = None, names_list: str = "") -> str:
    """Generate a name according to the configured naming strategy."""
    if name_mode == "counter":
        if counter is None:
            raise ValueError("counter is required when name_mode is 'counter'")
        return f"{prefix}{counter}{suffix}"

    if name_mode == "list":
        items = [item.strip() for item in names_list.replace("\n", ",").split(",") if item.strip()]
        if items:
            if counter is None:
                raise ValueError("counter is required when name_mode is 'list'")
            return items[(counter - 1) % len(items)]

    charset = ""
    if letters:
        charset += string.ascii_letters
    if digits:
        charset += string.digits
    if not charset:
        charset = string.ascii_letters

    lo = max(1, int(min_len))
    hi = max(lo, int(max_len))
    length = random.randint(lo, hi)
    return prefix + "".join(random.choices(charset, k=length)) + suffix


def safe_json_value(value):
    return value if isinstance(value, (str, int, float, bool, type(None), list, dict)) else str(value)
