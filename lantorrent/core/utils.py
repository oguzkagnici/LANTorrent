# lantorrent/core/utils.py
def format_size(bytes_val):
    """Format a byte value into human-readable form with appropriate units."""
    units = ['B', 'KB', 'MB', 'GB', 'TB']
    size = float(bytes_val)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f}{unit}"
        size /= 1024

    return f"{size:.1f}{unit}"