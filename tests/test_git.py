from pathlib import Path

import pytest

import woodglue.utils.git as git
from woodglue.misc import tabula_rasa_dir

wg_dir: Path = tabula_rasa_dir("build/tests/git/woodglue")

@pytest.mark.debug
def test_git():
    assert git.check_if_git_is_installed()
    git.clone_repo("https://github.com/walnutgeek/woodglue.git", wg_dir)
    assert wg_dir.is_dir()
    assert (wg_dir / "README.md").is_file()
    assert git.check_if_git_repo(wg_dir)
    assert git.get_branch_name(wg_dir) == "main"
    assert len(git.get_commit_hash(wg_dir)) == 40
    assert not git.check_if_remote_has_changes(wg_dir)  # no changes
    git.pull_latest(wg_dir)
    is_branch_up_to_date, is_tree_clean, actual_status = git.get_status(wg_dir)
    print(actual_status)
    assert is_branch_up_to_date and is_tree_clean
    abc = wg_dir / "ABC.md"
    abc.write_text("# ABC of WoodGlue.")
    assert abc.is_file()
    is_branch_up_to_date, is_tree_clean, actual_status = git.get_status(wg_dir)
    print(actual_status)
    assert is_branch_up_to_date and not is_tree_clean
    git.reset_to_remote(wg_dir)
    git.clean_working_tree(wg_dir)
    is_branch_up_to_date, is_tree_clean, actual_status = git.get_status(wg_dir)
    print(actual_status)
    assert is_branch_up_to_date and is_tree_clean
