from linux_wow_recorder import addon


def wow(tmp_path):
    """A WoW folder: <tmp>/_retail_ with its Logs and WTF."""
    game = tmp_path / "_retail_"
    (game / "Logs").mkdir(parents=True)
    (game / "WTF").mkdir()
    return game / "Logs"


def test_the_addon_ships_with_the_app():
    assert (addon.SOURCE / f"{addon.NAME}.toc").is_file()
    assert (addon.SOURCE / f"{addon.NAME}.lua").is_file()
    assert addon.version(addon.SOURCE)


def test_install_into_the_wow_folder_of_the_combat_logs(tmp_path):
    logs = wow(tmp_path)
    assert addon.status(logs) == addon.MISSING
    assert addon.install(logs) is None
    installed = tmp_path / "_retail_" / "Interface" / "AddOns" / addon.NAME
    assert (installed / f"{addon.NAME}.lua").read_bytes() == \
        (addon.SOURCE / f"{addon.NAME}.lua").read_bytes()
    assert addon.status(logs) == addon.INSTALLED


def test_not_a_wow_folder(tmp_path):
    (tmp_path / "Logs").mkdir()
    assert addon.status(tmp_path / "Logs") == addon.NO_WOW
    assert addon.status(None) == addon.NO_WOW
    assert addon.install(tmp_path / "Logs")   # says why
    assert not (tmp_path / "Interface").exists()


def test_an_older_installed_copy_is_updated_and_a_missing_one_left_alone(tmp_path):
    logs = wow(tmp_path)
    addon.refresh(logs)
    assert addon.status(logs) == addon.MISSING   # the user installs it, not the service
    addon.install(logs)
    toc = tmp_path / "_retail_" / "Interface" / "AddOns" / addon.NAME / f"{addon.NAME}.toc"
    toc.write_text(toc.read_text().replace(f"## Version: {addon.version(addon.SOURCE)}",
                                           "## Version: 0"))
    assert addon.status(logs) == addon.OLD
    addon.refresh(logs)
    assert addon.status(logs) == addon.INSTALLED


def test_remove(tmp_path):
    logs = wow(tmp_path)
    assert addon.remove(logs) is None
    addon.install(logs)
    gone = addon.remove(logs)
    assert gone and not gone.exists() and gone.parent.is_dir()   # AddOns itself stays
