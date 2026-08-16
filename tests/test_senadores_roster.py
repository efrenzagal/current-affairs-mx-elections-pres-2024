import unittest

from camara_de_senadores.composicion.crawl_senadores_roster import parse_senadores


class SenadoresRosterParserTests(unittest.TestCase):
    def test_parse_senadores_uses_card_party_and_official_profile_id(self):
        html = """
        <div class="perfil-senador border1MORENA" data_id-senador="1579">
          <span class="estado">Sonora</span>
          <h4 class="nombre-sen"><a href="/66/senador/1579">Sen. Heriberto Marcelo Aguilar Castillo</a></h4>
        </div>
        """
        parsed = parse_senadores(html)
        self.assertEqual(parsed.iloc[0]["member_source_id"], "1579")
        self.assertEqual(parsed.iloc[0]["current_party"], "MORENA")
        self.assertEqual(parsed.iloc[0]["current_name"], "Heriberto Marcelo Aguilar Castillo")
        self.assertEqual(parsed.iloc[0]["state"], "Sonora")


if __name__ == "__main__":
    unittest.main()
