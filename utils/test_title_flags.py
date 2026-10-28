"""Behavioral cases for deterministic title signals, including false friends."""
import unittest
from utils.title_flags import military_flags, VERSION


class TitleFlagTests(unittest.TestCase):
    def test_saved_titles_and_word_forms(self):
        for title in ['Сборщик ударных бпла "Герань-2" в цех (Донецк)',
                      'Военный/тыл/доброволец/выплаты/ЛНР/ДНР',
                      'Автоперегонщик — тыловая автослужба',
                      'Водитель ВАИ без опыта', 'Военнослужащий',
                      'Работа в Донецкой области', 'Водитель (СВО)']:
            with self.subTest(title=title):
                result = military_flags(title)
                self.assertEqual(result['military'], 'True')
                self.assertTrue(result['military_keywords'])
                self.assertEqual(result['military_rule_version'], VERSION)

    def test_contract_and_substrings_are_not_keywords(self):
        for title in ['Контракт-менеджер', 'Работа по контракту', 'Свой автомобиль',
                      'Освоение производства', 'Водитель категории B, C, E (Долги уйдут)',
                      'Служба водителем: выплата 3,1 млн, спишем долги',
                      'Курьер', 'Доброволец в приют']:
            with self.subTest(title=title):
                self.assertEqual(military_flags(title)['military'], 'False')
                self.assertEqual(military_flags(title)['military_keywords'], '')

    def test_case_spacing_deduplication_and_boundaries(self):
        self.assertEqual(military_flags('БПЛА / бпла — ДНР; СВО')['military_keywords'], 'БПЛА; ДНР; СВО')
        self.assertEqual(military_flags('БПЛАводитель')['military'], 'False')
        self.assertEqual(military_flags('ДНРовский')['military'], 'False')
        self.assertEqual(military_flags('Тыловой водитель')['military_keywords'], 'тыловой')

    def test_missing_title_is_not_a_negative_classification(self):
        for value in ['', '  ', None]:
            with self.assertRaises(ValueError):
                military_flags(value)
