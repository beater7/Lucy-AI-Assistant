import os
import re
from pathlib import Path

from config import FOLDER_SEARCH_MAX_DEPTH, FOLDER_SEARCH_MAX_DIRECTORIES, USER_FOLDER_NAMES


def user_folder_roots():
    home = Path.home()
    roots = []
    seen_paths = set()
    for names in USER_FOLDER_NAMES.values():
        for name in names:
            path = home / name
            if path.is_dir():
                normalized_path = os.path.normcase(os.path.abspath(path))
                if normalized_path not in seen_paths:
                    roots.append(path)
                    seen_paths.add(normalized_path)
                break
    return roots

def normalize_folder_query(query):
    normalized = re.sub(r"\s+", " ", query).strip().casefold()
    normalized = re.sub(r"^(?:my|the)\s+", "", normalized)
    normalized = re.sub(r"\s+(?:folder|directory)$", "", normalized)
    return normalized.strip()

def find_user_folders(query):
    folder_name = normalize_folder_query(query)
    if not folder_name:
        raise ValueError("Tell me which folder you're looking for.")

    parent_match = re.fullmatch(
        r"(.+?)\s+(?:in|inside|under|within)\s+(.+)",
        folder_name,
    )
    if parent_match:
        folder_name, parent_query = parent_match.groups()
        parent_matches, parent_search_limit_reached = find_user_folders(
            parent_query
        )
        if len(parent_matches) != 1:
            format_folder_matches(
                parent_query,
                parent_matches,
                parent_search_limit_reached,
            )
            raise ValueError(
                f"I couldn't locate the parent folder {parent_query}."
            )
        roots = parent_matches
    else:
        roots = user_folder_roots()

    for canonical_name, aliases in USER_FOLDER_NAMES.items():
        if folder_name in {alias.casefold() for alias in aliases}:
            folder_name = canonical_name
            break

    if folder_name in USER_FOLDER_NAMES and not parent_match:
        matching_roots = [
            root
            for root in roots
            if root.name.casefold() in
            {name.casefold() for name in USER_FOLDER_NAMES[folder_name]}
        ]
        return matching_roots, False

    matches = []
    directories_examined = 0
    search_limit_reached = False
    for root in roots:
        pending = [(root, 0)]
        while pending:
            parent, depth = pending.pop()
            if depth >= FOLDER_SEARCH_MAX_DEPTH:
                continue
            try:
                with os.scandir(parent) as entries:
                    for entry in entries:
                        if not entry.is_dir(follow_symlinks=False):
                            continue
                        try:
                            attributes = entry.stat(follow_symlinks=False).st_file_attributes
                        except AttributeError:
                            attributes = 0
                        except OSError as error:
                            print(f"Couldn't inspect folder entry {entry.name}: {error}")
                            continue
                        if entry.is_symlink() or attributes & 0x400:
                            continue

                        directories_examined += 1
                        folder_path = Path(entry.path)
                        if entry.name.casefold() == folder_name:
                            matches.append(folder_path)
                        if directories_examined >= FOLDER_SEARCH_MAX_DIRECTORIES:
                            search_limit_reached = True
                            break
                        pending.append((folder_path, depth + 1))
            except OSError as error:
                print(f"Couldn't search folders in {parent}: {error}")
                continue
            if search_limit_reached:
                break
        if search_limit_reached:
            break
    return matches, search_limit_reached

def format_folder_matches(query, matches, search_limit_reached):
    if not matches:
        detail = " I reached the folder search limit." if search_limit_reached else ""
        raise ValueError(f"I couldn't find a folder named {query}.{detail}")
    if len(matches) > 1:
        paths = "; ".join(str(path) for path in matches[:5])
        extra = f" I found {len(matches)} matches: {paths}."
        if len(matches) > 5:
            extra += " There are more matches."
        raise ValueError(
            f"I found more than one folder named {query}.{extra} "
            "Please say its parent folder, such as 'Projects in Documents'."
        )
    if search_limit_reached:
        print("Folder search reached its directory limit.")
    return matches[0]

def browse_user_folders(query):
    if not normalize_folder_query(query):
        roots = user_folder_roots()
        if not roots:
            return "I couldn't find any of your standard folders."
        names = ", ".join(root.name for root in roots)
        return f"Your available folders are: {names}. Which one would you like to browse?"

    folder_path = format_folder_matches(
        query,
        *find_user_folders(query),
    )
    subfolders = []
    try:
        with os.scandir(folder_path) as entries:
            for entry in entries:
                try:
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                    attributes = getattr(
                        entry.stat(follow_symlinks=False),
                        "st_file_attributes",
                        0,
                    )
                except OSError as error:
                    print(f"Couldn't inspect folder entry {entry.name}: {error}")
                    continue
                if not entry.is_symlink() and not attributes & 0x400:
                    subfolders.append(entry.name)
        subfolders.sort(key=str.casefold)
    except OSError as error:
        raise RuntimeError(f"I couldn't list folders in {folder_path}: {error}") from error

    if not subfolders:
        return f"There are no subfolders in {folder_path.name}."
    listed_names = ", ".join(subfolders[:20])
    if len(subfolders) > 20:
        listed_names += f", and {len(subfolders) - 20} more"
    return f"Folders in {folder_path.name}: {listed_names}."

def open_user_folder(query):
    folder_path = format_folder_matches(
        query,
        *find_user_folders(query),
    )
    try:
        os.startfile(str(folder_path))
    except OSError as error:
        raise RuntimeError(f"I couldn't open {folder_path}: {error}") from error
    return f"Opened {folder_path.name}."
