"""Marcas e modelos comuns no Brasil, com o tipo de cada um. Usado nas opções da tela de estoque."""

CATALOGO: dict[str, dict[str, str]] = {
    "Chevrolet": {"Onix": "Hatch", "Onix LT": "Hatch", "Onix Plus": "Sedã", "Onix Plus Premier": "Sedã",
                  "Cruze": "Sedã", "Tracker": "SUV", "Equinox": "SUV", "Spin": "SUV", "Montana": "Picape",
                  "S10": "Picape", "S10 LTZ Diesel": "Picape", "Trailblazer": "SUV"},
    "Fiat": {"Mobi": "Hatch", "Argo": "Hatch", "Argo Drive 1.0": "Hatch", "Cronos": "Sedã", "Cronos Drive": "Sedã",
             "Pulse": "SUV", "Fastback": "SUV", "Strada": "Picape", "Toro": "Picape", "Toro Volcano Diesel": "Picape",
             "Uno": "Hatch"},
    "VW": {"Gol": "Hatch", "Polo": "Hatch", "Polo Highline": "Hatch", "Virtus": "Sedã", "Jetta": "Sedã",
           "Nivus": "SUV", "T-Cross": "SUV", "T-Cross Comfortline": "SUV", "Taos": "SUV", "Tiguan": "SUV",
           "Saveiro": "Picape", "Amarok": "Picape"},
    "Hyundai": {"HB20": "Hatch", "HB20 Comfort": "Hatch", "HB20S": "Sedã", "Creta": "SUV",
                "Creta Limited": "SUV", "Tucson": "SUV"},
    "Toyota": {"Yaris": "Hatch", "Corolla": "Sedã", "Corolla XEi": "Sedã", "Corolla Cross": "SUV",
               "Corolla Cross XRE": "SUV", "RAV4": "SUV", "SW4": "SUV", "Hilux": "Picape"},
    "Honda": {"City": "Sedã", "Civic": "Sedã", "Civic Touring": "Sedã", "Fit": "Hatch", "HR-V": "SUV",
              "HR-V EXL": "SUV", "WR-V": "SUV", "ZR-V": "SUV"},
    "Nissan": {"Versa": "Sedã", "Sentra": "Sedã", "Sentra Advance": "Sedã", "Kicks": "SUV", "Kicks SV": "SUV",
               "Frontier": "Picape"},
    "Renault": {"Kwid": "Hatch", "Kwid Zen": "Hatch", "Sandero": "Hatch", "Logan": "Sedã", "Duster": "SUV",
                "Duster Iconic": "SUV", "Captur": "SUV", "Oroch": "Picape"},
    "Jeep": {"Renegade": "SUV", "Compass": "SUV", "Compass Longitude": "SUV", "Commander": "SUV"},
    "Ford": {"Ka": "Hatch", "EcoSport": "SUV", "Territory": "SUV", "Ranger": "Picape", "Ranger XLS": "Picape",
             "Maverick": "Picape"},
    "Peugeot": {"208": "Hatch", "2008": "SUV", "3008": "SUV"},
    "Citroën": {"C3": "Hatch", "C3 Aircross": "SUV", "C4 Cactus": "SUV"},
    "Mitsubishi": {"L200": "Picape", "Outlander": "SUV", "ASX": "SUV"},
    "BYD": {"Dolphin": "Hatch", "Song Plus": "SUV", "Seal": "Sedã"},
    "Caoa Chery": {"Tiggo 5X": "SUV", "Tiggo 7": "SUV", "Arrizo 6": "Sedã"},
}


def catalogo_com_estoque(carros: list[dict]) -> dict[str, dict[str, str]]:
    """Catálogo fixo + marcas e modelos que já estão no estoque."""
    cat = {m: dict(mods) for m, mods in CATALOGO.items()}
    for c in carros:
        cat.setdefault(c["marca"], {}).setdefault(c["modelo"], c["tipo"])
    return dict(sorted(cat.items()))
