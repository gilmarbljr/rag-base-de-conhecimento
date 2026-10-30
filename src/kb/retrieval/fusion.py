from collections.abc import Sequence


def reciprocal_rank_fusion(rankings: Sequence[Sequence[str]], k: int = 60) -> list[tuple[str, float]]:
    """Combina rankings pela posição de cada item: score = soma de 1 / (k + posição).

    Não depende da escala dos scores de cada recuperador (cosseno e ts_rank não são
    comparáveis), só da ordem. Empates são desfeitos pelo ID, para o resultado ser estável.
    """
    scores: dict[str, float] = {}
    for ranking in rankings:
        for position, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + position)
    return sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))
