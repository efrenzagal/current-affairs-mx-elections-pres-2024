import unittest

from aux_scripts.congress_rosters.crawl_congress_rosters import (
    parse_diputados_group,
    parse_senadores,
)
from ingestion.congress_roster_ingest import canonical_party, state_key
from ui.person_names import match_person_name


class CongressRosterParserTests(unittest.TestCase):
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


    def test_roster_aliases_are_canonical(self):
        self.assertEqual(canonical_party("MRN"), "MORENA")
        self.assertEqual(canonical_party("CAND_INDEPENDIENTE"), "IND")
        self.assertEqual(state_key("CDMX"), "CIUDAD DE MEXICO")
        self.assertEqual(state_key("Veracruz"), "VERACRUZ DE IGNACIO DE LA LLAVE")


if __name__ == "__main__":
    unittest.main()


class PersonNameMatchingTests(unittest.TestCase):
    """The Gaceta abbreviates trailing given names; the directories spell them out."""

    def test_abbreviated_given_name_matches_its_initial(self):
        candidates = ["Gómez Villalobos Tecutli José G.", "Romero Gomez Petra"]
        matched, quality = match_person_name(
            "Gómez Villalobos Tecutli José Guadalupe", candidates
        )
        self.assertEqual(matched, "Gómez Villalobos Tecutli José G.")
        self.assertEqual(quality, "initials")

    def test_initials_tier_survives_reordered_names(self):
        matched, quality = match_person_name(
            "César Alejandro Domínguez Domínguez", ["Domínguez Domínguez César A."]
        )
        self.assertEqual(matched, "Domínguez Domínguez César A.")
        self.assertEqual(quality, "initials")

    def test_exact_match_still_wins_over_an_initial(self):
        candidates = ["Guerrero Esquivel Fuensanta G.", "Guerrero Esquivel Fuensanta Guadalupe"]
        matched, quality = match_person_name(
            "Guerrero Esquivel Fuensanta Guadalupe", candidates
        )
        self.assertEqual(matched, "Guerrero Esquivel Fuensanta Guadalupe")
        self.assertEqual(quality, "exact")

    def test_ambiguous_initial_refuses_to_guess(self):
        """An abbreviated name that fits two people must not pick whichever came first."""
        matched, quality = match_person_name(
            "Pérez Ruiz Ana G.",
            ["Pérez Ruiz Ana Gabriela", "Pérez Ruiz Ana Guadalupe"],
        )
        self.assertIsNone(matched)
        self.assertEqual(quality, "ambiguous")

    def test_ambiguity_does_not_fall_through_to_the_fuzzy_tier(self):
        """Jaccard would happily rank one of the two first; the tier must stop instead."""
        matched, _ = match_person_name(
            "Pérez Ruiz Ana G.",
            ["Pérez Ruiz Ana Gabriela", "Pérez Ruiz Ana Guadalupe", "Otro Nombre Distinto"],
        )
        self.assertIsNone(matched)

    def test_a_different_name_is_not_matched_by_a_shared_initial(self):
        matched, _ = match_person_name(
            "Gómez Villalobos Tecutli José Guadalupe", ["Gómez Maldonado Maiella Martha G."]
        )
        self.assertIsNone(matched)
