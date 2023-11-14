import nltk
import spacy
from nltk import WordNetLemmatizer
from sentence_transformers import SentenceTransformer

from .set_match_evaluation import eval_set_match
from .spice_evaluation import eval_spice
from .soft_spice_evaluation import *
from ..utils import is_graph_format, clean_graph_string, space_out_symbols_in_graph
import logging

# Set up logging configuration (adjust the level and format as needed)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


class Evaluator:
    def __init__(self, parser=None, text_encoder_checkpoint=None, device='cuda:0', lemmatize=False):

        self.parser = parser
        if text_encoder_checkpoint:
            self.text_encoder = SentenceTransformer(text_encoder_checkpoint).to(device).eval()
        self.lemmatize = lemmatize

        if lemmatize:
            self.lemmatizer = WordNetLemmatizer()

    def _process_graphs(self, graph_string):
        """
        Perform text processing: lemmatization.

        :param text: A string containing the text to be processed.
        :return: Processed text as a string.
        """
        if self.lemmatize:
            # Lemmatize each word in the text
            tokens = graph_string.split(' ')
            graph_string = ' '.join([self.lemmatizer.lemmatize(token) for token in tokens])

        return graph_string

    def evaluate(self, candidates, references, method='spice', batch_size=4, return_graphs=False, **kwargs):
        """
        Evaluate scene graphs or text captions.

        :param candidates: List of candidate scene graphs or captions.
        :param references: List of List of reference scene graphs or captions.
        :param method: Evaluation method ('spice', 'soft_spice', or 'set_match').
        :param batch_size: Batch size for processing.
        :param kwargs: Additional arguments for evaluation metrics.
        :return: Evaluation scores, and optionally the processed candidates and references.
        """
        logging.info("Starting evaluation...")

        # Determine input formats and parse if necessary
        candidates, references = self._parse_inputs(candidates, references, batch_size, **kwargs)

        # Choose the evaluation method
        method_function = {
            'set_match': self._set_match_score,
            'spice': self._spice_score,
            'soft_spice': self._soft_spice_score
        }.get(method)

        logging.info(f"Evaluating using method: {method}")

        if method_function is None:
            raise ValueError(f"Unknown evaluation method: {method}")

        # Evaluate using the selected method
        scores = method_function(candidates, references, batch_size) if method == 'soft_spice' else method_function(
            candidates, references)

        logging.info("Evaluation completed.")

        # multiply with 100
        scores = [100 * score for score in scores]

        # Return results
        return (scores, candidates, references) if return_graphs else scores

    def _parse_inputs(self, candidates, references, batch_size, **kwargs):
        """
        Parse inputs if they are not in graph format.

        :param candidates: List of candidate scene graphs or captions.
        :param references: List of List of reference scene graphs or captions.
        :param batch_size: Batch size for processing.
        :param kwargs: Additional arguments for parsing.
        :return: Parsed candidates and references.
        """
        # Check for parser availability for non-graph formats
        if not self._all_items_are_graphs(candidates) and self.parser is None:
            raise ValueError("Parser is required for non-graph candidate inputs.")

        # Ensure the structure of references is correct
        assert all(isinstance(ref_list, list) for ref_list in
                   references), "Each reference should be a list of scene graphs or captions."

        # Parse candidates and references if they are not in graph format
        parsed_candidates = self._parse_if_needed(candidates, batch_size, is_nested=False, **kwargs)
        parsed_references = self._parse_if_needed(references, batch_size, is_nested=True, **kwargs)

        return parsed_candidates, parsed_references

    def _all_items_are_graphs(self, items):
        """
        Check if all items in a list are in graph format.

        :param items: List of items (candidates or references).
        :return: Boolean indicating if all items are in graph format.
        """
        return all(is_graph_format(item) for item in items)

    def _parse_if_needed(self, items, batch_size, is_nested, **kwargs):
        """
        Parse items if they are not in graph format. Handles both nested and non-nested lists.
        Applies lemmatization to parsed graphs if enabled.

        :param items: List or list of lists of items (candidates or references).
        :param batch_size: Batch size for processing.
        :param is_nested: Boolean indicating if the items list is nested.
        :param kwargs: Additional arguments for parsing.
        :return: Parsed items, maintaining the original structure.
        """
        # Determine whether parsing is needed
        needs_parsing = not all(
            is_graph_format(item) for sublist in (items if is_nested else [items]) for item in sublist)

        if needs_parsing:
            if is_nested:
                logging.info("Parsing references...")
            else:
                logging.info("Parsing candidates...")

        # Flatten nested list if necessary and parse
        flat_list, structure = (self._flatten_nested_list(items) if is_nested else (items, None))
        parsed_flat_list = self.parser.parse(flat_list, batch_size=batch_size, return_text=True,
                                             **kwargs) if needs_parsing else flat_list

        # Apply lemmatization post-processing if enabled
        if self.lemmatize:
            parsed_flat_list = [self._process_graphs(graph_str) for graph_str in parsed_flat_list]

        parsed_flat_list = [space_out_symbols_in_graph(graph_str) for graph_str in parsed_flat_list]

        # Recover the nested list structure if it was flattened
        return self._recover_nested_list_structure(parsed_flat_list, structure) if is_nested else parsed_flat_list

    def _flatten_nested_list(self, nested_list):
        """
        Flatten a nested list while keeping track of the original structure.

        :param nested_list: A list of lists to be flattened.
        :return: A tuple of the flattened list and a list of lengths of the original sublists.
        """
        flat_list = []
        structure = []
        for sublist in nested_list:
            flat_list.extend(sublist)
            structure.append(len(sublist))
        return flat_list, structure

    def _recover_nested_list_structure(self, flat_list, structure):
        """
        Recover the structure of a nested list from the flattened version and the original structure information.

        :param flat_list: Flattened list of items.
        :param structure: List of lengths of the original sublists.
        :return: Nested list reconstructed from the flat list.
        """
        nested_list, index = [], 0
        for length in structure:
            nested_list.append(flat_list[index:index + length])
            index += length
        return nested_list

    def _set_match_score(self, candidates, references):
        """
        Set the match score for each candidate and reference pair.

        :param candidates: Candidate scene graphs.
        :param references: Reference scene graphs.
        :return: List of match scores.
        """
        scores = []
        for cand, refs in zip(candidates, references):
            score = eval_set_match(cand, refs)
            scores.append(score)
        return scores

    def _spice_score(self, candidates, references):
        """
        Compute SPICE score.

        :param candidates: List of Candidate scene graphs.
        :param references: List of List of Reference scene graphs.
        :return: List of SPICE scores.
        """
        scores = []
        for cand, refs in zip(candidates, references):
            score = eval_spice(cand, refs)
            scores.append(score)
        return scores

    def _soft_spice_score(self, candidates, references, batch_size):
        """
        Compute Soft SPICE scores for a batch of candidates and references.

        :param candidates: A list of candidate scene graphs.
        :param references: A list of reference scene graphs corresponding to the candidates.
        :param batch_size: Batch size to be used for encoding.
        :return: A list of Soft SPICE scores for each candidate.
        """
        all_cand_phrases, all_ref_phrases, cand_lengths, ref_lengths = accumulate_phrases(candidates, references)
        encoded_cands, encoded_refs = encode_phrases(self.text_encoder, all_cand_phrases, all_ref_phrases, batch_size)
        scores = compute_scores(encoded_cands, encoded_refs, cand_lengths, ref_lengths)
        return scores


