import unittest

from ingestion.person_names import match_person_name


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


if __name__ == "__main__":
    unittest.main()
