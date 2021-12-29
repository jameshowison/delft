import itertools
import regex as re
import numpy as np
# seed is fixed for reproducibility
np.random.seed(7)

from unidecode import unidecode
from delft.utilities.Tokenizer import tokenizeAndFilterSimple
#import delft.utilities.bert.tokenization as tokenization
#import delft.utilities.bert.tokenization.bert_tokenization as tokenization

special_character_removal = re.compile(r'[^A-Za-z\.\-\?\!\,\#\@\% ]',re.IGNORECASE)


def to_vector_single(text, embeddings, maxlen=300):
    """
    Given a string, tokenize it, then convert it to a sequence of word embedding 
    vectors with the provided embeddings, introducing <PAD> and <UNK> padding token
    vector when appropriate
    """
    tokens = tokenizeAndFilterSimple(clean_text(text))
    window = tokens[-maxlen:]

    # TBD: use better initializers (uniform, etc.) 
    x = np.zeros((maxlen, embeddings.embed_size), )

    # TBD: padding should be left and which vector do we use for padding? 
    # and what about masking padding later for RNN?
    for i, word in enumerate(window):
        x[i,:] = embeddings.get_word_vector(word).astype('float32')

    return x

def clean_text(text):
    x_ascii = unidecode(text)
    x_clean = special_character_removal.sub('',x_ascii)
    return x_clean

def lower(word):
    return word.lower() 

def normalize_num(word):
    return re.sub(r'[0-9０１２３４５６７８９]', r'0', word)

def create_single_input_bert_old(text, maxlen=512, tokenizer=None):
    # TBD: exception if tokenizer is not valid/None

    piece_tokens = tokenizer.tokenize(text)
    piece_tokens = piece_tokens[:maxlen-2]
    piece_tokens = ["[CLS]"] + piece_tokens + ["[SEP]"]
    
    ids = get_ids(piece_tokens, tokenizer, maxlen)
    masks = get_masks(piece_tokens, maxlen)
    segments = get_segments(piece_tokens, maxlen)

    return ids, masks, segments

def create_single_input_bert(text, maxlen=512, tokenizer=None):
    # TBD: exception if tokenizer is not valid/None

    encoded_tokens = tokenizer.encode_plus(text, truncation=True, add_special_tokens=True, 
                                                max_length=maxlen, padding='max_length')
    # note: [CLS] and [SEP] are added by the tokenizer

    ids = encoded_tokens["input_ids"]
    masks = encoded_tokens["token_type_ids"]
    segments = encoded_tokens["attention_mask"]

    return ids, masks, segments

def create_batch_input_bert(texts, maxlen=512, tokenizer=None):
    # TBD: exception if tokenizer is not valid/None

    encoded_tokens = tokenizer.batch_encode_plus(texts, add_special_tokens=True, truncation=True, 
                                                max_length=maxlen, padding='max_length')
    #print(encoded_tokens)
    # note: [CLS] and [SEP] are added by the tokenizer

    ids = encoded_tokens["input_ids"]
    masks = encoded_tokens["token_type_ids"]
    segments = encoded_tokens["attention_mask"]

    return ids, masks, segments

def get_ids(tokens, tokenizer, maxlen):
    """
    Token ids from vocab
    """
    token_ids = tokenizer.convert_tokens_to_ids(tokens,)
    input_ids = token_ids + [0] * (maxlen-len(token_ids))
    return input_ids

def get_masks(tokens, maxlen):
    return [1]*len(tokens) + [0] * (maxlen - len(tokens))

def get_segments(tokens, maxlen):
    """
    Segments: 0 for the first sequence, 1 for the second
    """
    segments = []
    current_segment_id = 0
    for token in tokens:
        segments.append(current_segment_id)
        if token == "[SEP]":
            current_segment_id = 1
    return segments + [0] * (maxlen - len(tokens))

'''
class InputExample(object):
  """
  From official BERT implementation.
  A single training/test example for simple sequence classification.
  """

  def __init__(self, guid, text_a, text_b=None, label=None):
    """Constructs a InputExample.

    Args:
      guid: Unique id for the example.
      text_a: string. The untokenized text of the first sequence. For single
        sequence tasks, only this sequence must be specified.
      text_b: (Optional) string. The untokenized text of the second sequence.
        Only must be specified for sequence pair tasks.
      label: (Optional) string. The label of the example. This should be
        specified for train and dev examples, but not for test examples.
    """
    self.guid = guid
    self.text_a = text_a
    self.text_b = text_b
    self.label = label


class DataProcessor(object):
    """
    Base class for data converters for sequence classification data sets.
    Derived from official BERT implementation.
    """

    def get_train_examples(self, data_dir):
        """
        Gets a collection of `InputExample`s for the train set.
        """
        raise NotImplementedError()

    def get_dev_examples(self, data_dir):
        """
        Gets a collection of `InputExample`s for the dev set.
        """
        raise NotImplementedError()

    def get_test_examples(self, data_dir):
        """
        Gets a collection of `InputExample`s for prediction.
        """
        raise NotImplementedError()

    def get_labels(self):
        """
        Gets the list of labels for this data set.
        """
        raise NotImplementedError()

    @classmethod
    def _read_tsv(cls, input_file, quotechar=None):
        """Reads a tab separated value file."""
        with tf.gfile.Open(input_file, "r") as f:
            reader = csv.reader(f, delimiter="\t", quotechar=quotechar)
            lines = []
            for line in reader:
                lines.append(line)
            return lines


class BERT_classifier_processor(DataProcessor):
    """
    BERT data processor for classification.
    Derived from official BERT implementation.
    Use the very fast TensorFlow Text sentence piece tokenizer.
    """
    def __init__(self, labels=None, x_train=None, y_train=None, x_test=None, y_test=None):
        self.list_classes = labels
        self.x_train = x_train
        self.y_train = y_train
        self.x_test = x_test
        self.y_test = y_test

    def get_train_examples(self, x_train=None, y_train=None):
        """
        See base class.
        """
        if x_train is not None:
            self.x_train = x_train
        if y_train is not None:
            self.y_train = y_train
        examples, _ = self.create_examples(self.x_train, self.y_train)
        return examples

    def get_labels(self):
        """
        See base class.
        """
        return self.list_classes

    def get_test_examples(self, x_test=None, y_test=None):
        """See base class."""
        if x_test is not None:
            self.x_test = x_test
        if y_test is not None:
            self.y_test = y_test
        examples, results = self.create_examples(self.x_test, self.y_test)
        return examples, results

    def create_examples(self, x_s, y_s=None):
        examples = []
        valid_classes = np.zeros((y_s.shape[0],len(self.list_classes)))
        accumul = 0
        for (i, x) in enumerate(x_s):
            y = y_s[i]
            guid = i
            text_a = self.convert_to_unicode(x)
            #the_class = self._rewrite_classes(y, i)
            ind, = np.where(y == 1)
            the_class = self.list_classes[ind[0]]
            if the_class is None:
                #print(text_a)
                continue
            if the_class not in self.list_classes:
                #the_class = 'other'
                continue
            label = self.convert_to_unicode(the_class)
            examples.append(InputExample(guid=guid, text_a=text_a, text_b=None, label=label))
            valid_classes[accumul] = y
            accumul += 1

        return examples, valid_classes 

    def create_inputs(self, x_s, dummy_label='dummy'):
        examples = []
        # dummy label to avoid breaking the bert base code
        label = self.convert_to_unicode(dummy_label)
        for (i, x) in enumerate(x_s):
            guid = i
            text_a = self.convert_to_unicode(x) 
            examples.append(InputExample(guid=guid, text_a=text_a, text_b=None, label=label))
        return examples

    def convert_to_unicode(self, text):
        """
        Converts input `text` to Unicode (if it's not already), assuming utf-8 input.
        """
        if isinstance(text, str):
            return text
        elif isinstance(text, bytes):
            return text.decode("utf-8", "ignore")
        else:
            raise ValueError("Unsupported string type: %s" % (type(text)))
'''