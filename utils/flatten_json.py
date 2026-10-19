import collections.abc

# Keys whose dict values should be treated as opaque leaf nodes (not recursively flattened).
# source_location contains page/offset metadata that must stay as a single dict.
_LEAF_KEYS = frozenset({"source_location"})


def flatten_json(d, parent_key='', sep='.'):
    """
    Recursively flattens a nested dictionary.

    Example:
    {'a': {'b': 1, 'c': 2}}
    becomes
    {'a.b': 1, 'a.c': 2}

    Keys in _LEAF_KEYS are preserved as-is (not recursively flattened).
    """
    items = []
    for k, v in d.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else k
        if isinstance(v, collections.abc.MutableMapping) and k not in _LEAF_KEYS:
            # If value is a dict (and not a leaf key), recurse
            items.extend(flatten_json(v, new_key, sep=sep).items())
        else:
            # If value is a leaf node (str, int, bool, etc.) or a leaf-key dict
            items.append((new_key, v))
    return dict(items)
