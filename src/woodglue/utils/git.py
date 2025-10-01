import subprocess
from pathlib import Path


def check_if_git_is_installed() -> bool:
    try:
        subprocess.check_output(["git", "--version"])
        return True
    except BaseException:
        return False


def get_branch_name(cwd: Path | str = ".") -> str:
    cwd = Path(cwd)
    return (
        subprocess.check_output(["git", "branch", "--show-current"], cwd=cwd)
        .decode("utf-8")
        .strip()
    )


def get_commit_hash(cwd: Path | str = ".") -> str:
    cwd = Path(cwd)
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=cwd).decode("utf-8").strip()


def check_if_git_repo(cwd: Path | str = ".") -> bool:
    cwd = Path(cwd)
    try:
        return (
            subprocess.check_output(["git", "rev-parse", "--is-inside-work-tree"], cwd=cwd)
            .decode("utf-8")
            .strip()
            == "true"
        )
    except BaseException:
        return False


def check_if_remote_has_changes(cwd: Path | str = ".") -> bool:
    cwd = Path(cwd)
    return (
        subprocess.check_output(
            ["git", "log", f"HEAD..origin/{get_branch_name(cwd)}", "--oneline"], cwd=cwd
        )
        .decode("utf-8")
        .strip()
        != ""
    )


def pull_latest(cwd: Path | str = ".") -> None:
    cwd = Path(cwd)
    subprocess.run(["git", "pull", "origin", get_branch_name(cwd)], cwd=cwd)


def clone_repo(repo_url: str, cwd: Path | str = ".") -> None:
    cwd = Path(cwd)
    subprocess.run(["git", "clone", repo_url, cwd])


def reset_to_remote(cwd: Path | str = ".") -> None:
    cwd = Path(cwd)
    subprocess.run(["git", "reset", "--hard", "origin/HEAD"], cwd=cwd)


def get_status(cwd: Path | str = ".") -> tuple[bool, bool, str]:
    cwd = Path(cwd)
    msg = subprocess.check_output(["git", "status"], cwd=cwd).decode("utf-8").strip()
    is_branch_up_to_date = "Your branch is up to date" in msg
    is_tree_clean = "nothing to commit, working tree clean" in msg
    return is_branch_up_to_date, is_tree_clean, msg

def clean_working_tree(cwd: Path | str = ".") -> None:
    cwd = Path(cwd)
    subprocess.run(["git", "clean", "-fxd"], cwd=cwd)