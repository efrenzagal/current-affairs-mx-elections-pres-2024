import unittest

from camara_de_diputados.composicion.crawl_diputados_roster import parse_diputados_group
from lib.canonical import canonical_party, state_key


class DiputadosRosterParserTests(unittest.TestCase):
    def test_parse_diputados_group_extracts_stable_profile_and_status(self):
        html = """
        <table><tr>
          <td><a href="curricula.php?dipt=391">1 Abreu Artiñano Rocío Adriana (LICENCIA)</a></td>
          <td> Campeche </td><td> Circ. 3 </td>
        </tr><tr>
          <td><a href="curricula.php?dipt=88">2 Acosta Trujillo Juana</a></td>
          <td> Guanajuato </td><td> Dtto. 14 </td>
        </tr></table>
        """
        parsed = parse_diputados_group(html, "MORENA", "https://example.test/list")
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed.iloc[0].to_dict()["member_source_id"], "391")
        self.assertEqual(parsed.iloc[0]["current_name"], "Abreu Artiñano Rocío Adriana")
        self.assertEqual(parsed.iloc[0]["circunscripcion"], 3)
        self.assertEqual(parsed.iloc[0]["status"], "licencia")
        self.assertEqual(parsed.iloc[1]["district"], 14)
        self.assertEqual(parsed.iloc[1]["status"], "en_funciones")

    def test_roster_aliases_are_canonical(self):
        self.assertEqual(canonical_party("MRN"), "MORENA")
        self.assertEqual(canonical_party("CAND_INDEPENDIENTE"), "IND")
        self.assertEqual(state_key("CDMX"), "CIUDAD DE MEXICO")
        self.assertEqual(state_key("Veracruz"), "VERACRUZ DE IGNACIO DE LA LLAVE")


if __name__ == "__main__":
    unittest.main()
