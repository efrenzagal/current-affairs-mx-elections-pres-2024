import unittest

from camara_de_senadores.votos.crawl_senado_votes import parse_vote_page


class SenadoVotePageTests(unittest.TestCase):
    def test_multiline_description_is_not_misread_as_vote_type(self):
        html = """
        <div class="col-sm-12 text-justify">
          Dictamen que concede autorización a la persona<br>
          titular del Poder Ejecutivo para permitir una salida.<br>
        </div>
        """
        row = parse_vote_page(1, html)
        self.assertEqual(
            row["description"],
            "Dictamen que concede autorización a la persona\n"
            "titular del Poder Ejecutivo para permitir una salida.",
        )
        self.assertIsNone(row["vote_type"])

    def test_bundled_matters_remain_in_description(self):
        html = """
        <div class="col-sm-12 text-justify">
          Dictamen sobre derechos humanos.<br><br>
          Dictamen sobre desarrollo rural.<br>
        </div>
        """
        row = parse_vote_page(2, html)
        self.assertEqual(
            row["description"],
            "Dictamen sobre derechos humanos.\nDictamen sobre desarrollo rural.",
        )
        self.assertIsNone(row["vote_type"])

    def test_multiline_vote_stage_is_kept_together(self):
        html = """
        <div class="col-sm-12 text-justify">
          Dictamen sobre justicia.<br><br>
          VOTACIÓN EN LO PARTICULAR DE LOS ARTÍCULOS 5 Y 7.<br>
          DE LOS ARTÍCULOS TRANSITORIOS PRIMERO Y SEGUNDO.
        </div>
        """
        row = parse_vote_page(3, html)
        self.assertEqual(row["description"], "Dictamen sobre justicia.")
        self.assertEqual(
            row["vote_type"],
            "VOTACIÓN EN LO PARTICULAR DE LOS ARTÍCULOS 5 Y 7.\n"
            "DE LOS ARTÍCULOS TRANSITORIOS PRIMERO Y SEGUNDO.",
        )


if __name__ == "__main__":
    unittest.main()
