"""Media-type inference shared by generated artifact pipelines."""


def media_type_for_path(path: str) -> str:
    lower = path.lower()
    if lower.endswith(".html"):
        return "text/html"
    if lower.endswith(".css"):
        return "text/css"
    if lower.endswith(".js"):
        return "application/javascript"
    if lower.endswith(".json"):
        return "application/json"
    if lower.endswith(".py"):
        return "text/x-python"
    return "text/plain"
