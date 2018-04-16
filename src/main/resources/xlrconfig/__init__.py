
def get_parent(path):
    if '/' in path:
        return path.rsplit('/', 1)[0]
    return None


def get_name(path):
    if '/' in path:
        return path.rsplit('/', 1)[1]
    return path
