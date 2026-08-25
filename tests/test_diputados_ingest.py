import unittest

from camara_de_diputados.escanos.ingest import resolve_gaceta_identity


class ResolveGacetaIdentityTests(unittest.TestCase):
    def test_audited_abbreviation_keeps_titular(self):
        result = resolve_gaceta_identity(
            "TECUTLI JOSE GUADALUPE GOMEZ VILLALOBOS",
            "VICENTE GARCIA CAMPOS",
            ["Gómez Villalobos Tecutli José G."],
        )

        self.assertEqual(result[0], "Gómez Villalobos Tecutli José G.")
        self.assertEqual(result[1], "titular")
        self.assertEqual(result[2], "audited_override")

    def test_exact_substitute_is_used_when_titular_is_absent(self):
        result = resolve_gaceta_identity(
            "HECTOR MELESIO CUEN OJEDA",
            "JUAN MORENO DE HARO",
            ["Moreno de Haro Juan"],
        )

        self.assertEqual(result[0], "Moreno de Haro Juan")
        self.assertEqual(result[1], "suplente")
        self.assertEqual(result[2], "exact_tokens")

    def test_ordinary_exact_titular_still_matches(self):
        result = resolve_gaceta_identity(
            "JUAN PEREZ LOPEZ",
            "",
            ["Pérez López Juan"],
        )

        self.assertEqual(result[0], "Pérez López Juan")
        self.assertEqual(result[1], "titular")
        self.assertEqual(result[2], "exact_tokens")

    def test_unknown_seat_remains_unmatched(self):
        result = resolve_gaceta_identity(
            "PERSONA SIN COINCIDENCIA",
            "SUPLENTE SIN COINCIDENCIA",
            ["Pérez López Juan"],
        )

        self.assertEqual(result, (None, None, "unmatched", None))


if __name__ == "__main__":
    unittest.main()
