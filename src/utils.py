from typing import TypeVar, Callable, Type

T = TypeVar("T")

def ensure_input(type: Type[T], parser: Callable[[str], T], prompt: str = "") -> T:
    while not isinstance(answer := parser(input(prompt)), type):
        pass
    return answer


def ask(default: bool = False, prompt: str = "") -> bool:
    while (answer := input(prompt).lower()) not in ["y", "n", "s", "yes", "si", "no", ""]:
        pass
    if answer in ["y", "s", "yes", "si"]:
        return True
    if answer in ["n", "no"]:
        return False
    if answer == "":
        return default
    raise Exception(f"invalid answer: {answer}")


def print_obj(obj: object) -> str:
    res = [type(obj).__name__]
    for attr in vars(obj):
        if not attr.startswith("_") and not attr.endswith("_"):
            res.append(f"    {attr}: {getattr(obj, attr)}")
    return "\n".join(res)