from datetime import date

from kb.config import Settings


def test_relogio_fixo_real_e_da_release():
    assert Settings(data_simulada="2026-02-15").hoje() == date(2026, 2, 15)
    assert Settings(data_simulada="").hoje() == date.today()

    release = Settings(data_simulada="release")
    assert release.hoje(release_date=date(2026, 3, 10)) == date(2026, 3, 10)
    assert release.hoje() == date.today()  # fonte ainda não registrada
