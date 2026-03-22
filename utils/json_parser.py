import json


def safe_json_parse(json_string, fallback=None):
    """Robust JSON parser with multiple recovery strategies."""
    if fallback is None:
        fallback = {}

    if not json_string or not isinstance(json_string, str):
        return fallback

    # Clean markdown fences first
    import re
    json_string = re.sub(r"```[a-zA-Z]*\n?", "",
                         json_string).replace("```", "")
    json_string = json_string.strip()

    # Strategy 1: Direct parsing
    try:
        result = json.loads(json_string)
        if isinstance(result, str) and result.strip().startswith(("{", "[")):
            return safe_json_parse(result, fallback)
        return result
    except json.JSONDecodeError:
        pass

    # Strategy 2: Clean common issues and handle nested single quotes
    try:
        cleaned = json_string.strip()
        cleaned = cleaned.replace('\n', '\\n').replace(
            '\r', '\\r').replace('\t', '\\t')
        cleaned = re.sub(r',(\s*[}\]])', r'\1', cleaned)

        if cleaned.startswith("'") or "': '" in cleaned or "': {'" in cleaned:
            cleaned = cleaned.replace("'", '"')
            cleaned = cleaned.replace('""', '"')
        else:
            cleaned = re.sub(r"'([^']*)':", r'"\1":', cleaned)
            cleaned = re.sub(r":\s*'([^']*)'", r': "\1"', cleaned)

        result = json.loads(cleaned)
        if isinstance(result, str) and result.strip().startswith(("{", "[")):
            return safe_json_parse(result, fallback)
        return result
    except (json.JSONDecodeError, AttributeError):
        pass

    # Strategy 3: Extract key-value pairs manually
    try:
        data = {}
        for match in re.finditer(r'"([^"]+)":\s*(\d+(?:\.\d+)?)', json_string):
            key, value = match.groups()
            data[key] = float(value) if '.' in value else int(value)

        for match in re.finditer(r'"([^"]+)":\s*"([^"]*)"', json_string):
            key, value = match.groups()
            data[key] = value

        for match in re.finditer(r'"([^"]+)":\s*(true|false)', json_string):
            key, value = match.groups()
            data[key] = value == 'true'

        if data:
            return data
    except:
        pass

    return fallback
