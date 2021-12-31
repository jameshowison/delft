from tensorflow.keras.layers import Dense, LSTM, GRU, Bidirectional, Embedding, Input, Dropout, Reshape
from tensorflow.keras.layers import GlobalMaxPooling1D, TimeDistributed, Conv1D
from tensorflow.keras.layers import Concatenate
from tensorflow.keras.models import Model
from tensorflow.keras.models import clone_model

from transformers import TFBertModel

from delft.utilities.crf_layer import ChainCRF

from delft.sequenceLabelling.preprocess import BERTPreprocessor
from delft.sequenceLabelling.data_generator import DataGenerator, DataGeneratorTransformers

import json
import time
import os
import shutil
import numpy as np
import tensorflow as tf


"""
The sequence labeling models.

Each model is a class implementing a Keras architecture. 
The class can also define the data generator class object to be used, the loss function,
the metrics and the optimizer.
"""

def get_model(config, preprocessor, ntags=None):
    """
    Return a model instance by its name. This is a facilitator function. 
    """
    if config.architecture == BidLSTM_CRF.name:
        preprocessor.return_casing = False
        config.use_crf = True
        return BidLSTM_CRF(config, ntags)
    elif config.architecture == BidLSTM_CNN.name:
        preprocessor.return_casing = True
        config.use_crf = False
        return BidLSTM_CNN(config, ntags)
    elif config.architecture == BidLSTM_CNN_CRF.name:
        preprocessor.return_casing = True
        config.use_crf = True
        return BidLSTM_CNN_CRF(config, ntags)
    elif config.architecture == BidGRU_CRF.name:
        preprocessor.return_casing = False
        config.use_crf = True
        return BidGRU_CRF(config, ntags)
    elif config.architecture == BidLSTM_CRF_FEATURES.name:
        preprocessor.return_casing = False
        preprocessor.return_features = True
        config.use_crf = True
        return BidLSTM_CRF_FEATURES(config, ntags)
    elif config.architecture == BidLSTM_CRF_CASING.name:
        preprocessor.return_casing = True
        config.use_crf = True
        return BidLSTM_CRF_CASING(config, ntags)
    elif config.architecture == BERT.name:
        preprocessor.return_word_embeddings = False
        preprocessor.return_casing = False
        preprocessor.return_features = False
        preprocessor.return_bert_embeddings = True
        config.use_crf = False
        config.labels = preprocessor.vocab_tag
        return BERT(config, ntags)
    elif config.architecture == BERT_CRF.name:
        preprocessor.return_word_embeddings = False
        preprocessor.return_casing = False
        preprocessor.return_features = False
        preprocessor.return_bert_embeddings = True
        config.use_crf = True
        config.labels = preprocessor.vocab_tag
        return BERT_CRF(config, ntags)
    elif config.architecture == BERT_CRF_FEATURES.name:
        preprocessor.return_word_embeddings = False
        preprocessor.return_casing = False
        preprocessor.return_features = True
        preprocessor.return_bert_embeddings = True
        config.use_crf = True
        config.labels = preprocessor.vocab_tag
        return BERT_CRF_FEATURES(config, ntags)

    else:
        raise (OSError('Model name does exist: ' + config.architecture))


class BaseModel(object):

    def __init__(self, config, ntags):
        self.config = config
        self.ntags = ntags
        self.model = None

    def predict(self, X, *args, **kwargs):
        y_pred = self.model.predict(X, batch_size=1)
        return y_pred

    def evaluate(self, X, y):
        score = self.model.evaluate(X, y, batch_size=1)
        return score

    def save(self, filepath):
        self.model.save_weights(filepath)

    def load(self, filepath):
        print('loading model weights', filepath)
        self.model.load_weights(filepath=filepath)

    def __getattr__(self, name):
        return getattr(self.model, name)

    def clone_model(self):
        model_copy = clone_model(self.model)
        model_copy.set_weights(self.model.get_weights())
        return model_copy

    def get_generator(self):
        # default generator
        return DataGenerator

class BidLSTM_CRF(BaseModel):
    """
    A Keras implementation of BidLSTM-CRF for sequence labelling.

    References
    --
    Guillaume Lample, Miguel Ballesteros, Sandeep Subramanian, Kazuya Kawakami, Chris Dyer.
    "Neural Architectures for Named Entity Recognition". Proceedings of NAACL 2016.
    https://arxiv.org/abs/1603.01360
    """
    name = 'BidLSTM_CRF'

    def __init__(self, config, ntags=None):

        # build input, directly feed with word embedding by the data generator
        word_input = Input(shape=(None, config.word_embedding_size), name='word_input')

        # build character based embedding
        char_input = Input(shape=(None, config.max_char_length), dtype='int32', name='char_input')
        char_embeddings = TimeDistributed(Embedding(input_dim=config.char_vocab_size,
                                    output_dim=config.char_embedding_size,
                                    #mask_zero=True,
                                    #embeddings_initializer=RandomUniform(minval=-0.5, maxval=0.5),
                                    name='char_embeddings'
                                    ))(char_input)

        chars = TimeDistributed(Bidirectional(LSTM(config.num_char_lstm_units, return_sequences=False)))(char_embeddings)

        # length of sequence not used by the model, but used by the training scorer
        length_input = Input(batch_shape=(None, 1), dtype='int32', name='length_input')

        # combine characters and word embeddings
        x = Concatenate()([word_input, chars])
        x = Dropout(config.dropout)(x)

        x = Bidirectional(LSTM(units=config.num_word_lstm_units,
                               return_sequences=True,
                               recurrent_dropout=config.recurrent_dropout))(x)
        x = Dropout(config.dropout)(x)
        x = Dense(config.num_word_lstm_units, activation='tanh')(x)
        x = Dense(ntags)(x)
        self.crf = ChainCRF()
        pred = self.crf(x)

        self.model = Model(inputs=[word_input, char_input, length_input], outputs=[pred])
        self.config = config


class BidLSTM_CNN(BaseModel):
    """
    A Keras implementation of BidLSTM-CNN for sequence labelling.

    References
    --
    Jason P. C. Chiu, Eric Nichols. "Named Entity Recognition with Bidirectional LSTM-CNNs". 2016. 
    https://arxiv.org/abs/1511.08308
    """

    name = 'BidLSTM_CNN'

    def __init__(self, config, ntags=None):

        # build input, directly feed with word embedding by the data generator
        word_input = Input(shape=(None, config.word_embedding_size), name='word_input')

        # build character based embedding        
        char_input = Input(shape=(None, config.max_char_length), dtype='int32', name='char_input')
        char_embeddings = TimeDistributed(
                                Embedding(input_dim=config.char_vocab_size,
                                    output_dim=config.char_embedding_size,
                                    mask_zero=False,
                                    name='char_embeddings'
                                    ))(char_input)

        dropout = Dropout(config.dropout)(char_embeddings)

        conv1d_out = TimeDistributed(Conv1D(kernel_size=3, filters=30, padding='same',activation='tanh', strides=1))(dropout)
        maxpool_out = TimeDistributed(GlobalMaxPooling1D())(conv1d_out)
        chars = Dropout(config.dropout)(maxpool_out)

        # custom features input and embeddings
        casing_input = Input(batch_shape=(None, None,), dtype='int32', name='casing_input')
        casing_embedding = Embedding(input_dim=config.case_vocab_size,
                           output_dim=config.case_embedding_size,
                           #mask_zero=True,
                           trainable=False,
                           name='casing_embedding')(casing_input)
        casing_embedding = Dropout(config.dropout)(casing_embedding)

        # length of sequence not used by the model, but used by the training scorer
        length_input = Input(batch_shape=(None, 1), dtype='int32')

        # combine words, custom features and characters
        x = Concatenate(axis=-1)([word_input, casing_embedding, chars])
        x = Dropout(config.dropout)(x)
        x = Bidirectional(LSTM(units=config.num_word_lstm_units, 
                               return_sequences=True, 
                               recurrent_dropout=config.recurrent_dropout))(x)
        x = Dropout(config.dropout)(x)
        #pred = TimeDistributed(Dense(ntags, activation='softmax'))(x)
        pred = Dense(ntags, activation='softmax')(x)

        self.model = Model(inputs=[word_input, char_input, casing_input, length_input], outputs=[pred])
        self.config = config


class BidLSTM_CNN_CRF(BaseModel):
    """
    A Keras implementation of BidLSTM-CNN-CRF for sequence labelling.

    References
    --
    Xuezhe Ma and Eduard Hovy. "End-to-end Sequence Labeling via Bi-directional LSTM-CNNs-CRF". 2016. 
    https://arxiv.org/abs/1603.01354
    """

    name = 'BidLSTM_CNN_CRF'

    def __init__(self, config, ntags=None):

        # build input, directly feed with word embedding by the data generator
        word_input = Input(shape=(None, config.word_embedding_size), name='word_input')

        # build character based embedding        
        char_input = Input(shape=(None, config.max_char_length), dtype='int32', name='char_input')
        char_embeddings = TimeDistributed(
                                Embedding(input_dim=config.char_vocab_size,
                                    output_dim=config.char_embedding_size,
                                    mask_zero=False,
                                    name='char_embeddings'
                                    ))(char_input)

        dropout = Dropout(config.dropout)(char_embeddings)

        conv1d_out = TimeDistributed(Conv1D(kernel_size=3, filters=30, padding='same',activation='tanh', strides=1))(dropout)
        maxpool_out = TimeDistributed(GlobalMaxPooling1D())(conv1d_out)
        chars = Dropout(config.dropout)(maxpool_out)

        # custom features input and embeddings
        casing_input = Input(batch_shape=(None, None,), dtype='int32', name='casing_input')

        """
        casing_embedding = Embedding(input_dim=config.case_vocab_size, 
                           output_dim=config.case_embedding_size,
                           mask_zero=True,
                           trainable=False,
                           name='casing_embedding')(casing_input)
        casing_embedding = Dropout(config.dropout)(casing_embedding)
        """

        # length of sequence not used by the model, but used by the training scorer
        length_input = Input(batch_shape=(None, 1), dtype='int32')

        # combine words, custom features and characters
        x = Concatenate(axis=-1)([word_input, chars])
        x = Dropout(config.dropout)(x)

        x = Bidirectional(LSTM(units=config.num_word_lstm_units, 
                               return_sequences=True, 
                               recurrent_dropout=config.recurrent_dropout))(x)
        x = Dropout(config.dropout)(x)
        x = Dense(config.num_word_lstm_units, activation='tanh')(x)
        x = Dense(ntags)(x)
        self.crf = ChainCRF()
        pred = self.crf(x)

        self.model = Model(inputs=[word_input, char_input, casing_input, length_input], outputs=[pred])
        self.config = config


class BidGRU_CRF(BaseModel):
    """
    A Keras implementation of BidGRU-CRF for sequence labelling.
    """

    name = 'BidGRU_CRF'

    def __init__(self, config, ntags=None):

        # build input, directly feed with word embedding by the data generator
        word_input = Input(shape=(None, config.word_embedding_size), name='word_input')

        # build character based embedding
        char_input = Input(shape=(None, config.max_char_length), dtype='int32', name='char_input')
        char_embeddings = TimeDistributed(Embedding(input_dim=config.char_vocab_size,
                                    output_dim=config.char_embedding_size,
                                    mask_zero=True,
                                    #embeddings_initializer=RandomUniform(minval=-0.5, maxval=0.5),
                                    name='char_embeddings'
                                    ))(char_input)

        chars = TimeDistributed(Bidirectional(LSTM(config.num_char_lstm_units, return_sequences=False)))(char_embeddings)

        # length of sequence not used by the model, but used by the training scorer
        length_input = Input(batch_shape=(None, 1), dtype='int32', name='length_input')

        # combine characters and word embeddings
        x = Concatenate()([word_input, chars])
        x = Dropout(config.dropout)(x)

        x = Bidirectional(GRU(units=config.num_word_lstm_units,
                               return_sequences=True,
                               recurrent_dropout=config.recurrent_dropout))(x)
        x = Dropout(config.dropout)(x)
        x = Bidirectional(GRU(units=config.num_word_lstm_units,
                               return_sequences=True,
                               recurrent_dropout=config.recurrent_dropout))(x)
        x = Dense(config.num_word_lstm_units, activation='tanh')(x)
        x = Dense(ntags)(x)
        self.crf = ChainCRF()
        pred = self.crf(x)

        self.model = Model(inputs=[word_input, char_input, length_input], outputs=[pred])
        self.config = config


class BidLSTM_CRF_CASING(BaseModel):
    """
    A Keras implementation of BidLSTM-CRF for sequence labelling with additinal features related to casing
    (inferred from word forms).
    """

    name = 'BidLSTM_CRF_CASING'

    def __init__(self, config, ntags=None):

        # build input, directly feed with word embedding by the data generator
        word_input = Input(shape=(None, config.word_embedding_size), name='word_input')

        # build character based embedding
        char_input = Input(shape=(None, config.max_char_length), dtype='int32', name='char_input')
        char_embeddings = TimeDistributed(Embedding(input_dim=config.char_vocab_size,
                                    output_dim=config.char_embedding_size,
                                    mask_zero=True,
                                    #embeddings_initializer=RandomUniform(minval=-0.5, maxval=0.5),
                                    name='char_embeddings'
                                    ))(char_input)

        chars = TimeDistributed(Bidirectional(LSTM(config.num_char_lstm_units, return_sequences=False)))(char_embeddings)

        # custom features input and embeddings
        casing_input = Input(batch_shape=(None, None,), dtype='int32', name='casing_input')

        casing_embedding = Embedding(input_dim=config.case_vocab_size, 
                           output_dim=config.case_embedding_size,
                           mask_zero=True,
                           trainable=False,
                           name='casing_embedding')(casing_input)
        casing_embedding = Dropout(config.dropout)(casing_embedding)

        # length of sequence not used by the model, but used by the training scorer
        length_input = Input(batch_shape=(None, 1), dtype='int32', name='length_input')

        # combine characters and word embeddings
        x = Concatenate()([word_input, casing_embedding, chars])
        x = Dropout(config.dropout)(x)

        x = Bidirectional(LSTM(units=config.num_word_lstm_units,
                               return_sequences=True, 
                               recurrent_dropout=config.recurrent_dropout))(x)
        x = Dropout(config.dropout)(x)
        x = Dense(config.num_word_lstm_units, activation='tanh')(x)
        x = Dense(ntags)(x)
        self.crf = ChainCRF()
        pred = self.crf(x)

        self.model = Model(inputs=[word_input, char_input, casing_input, length_input], outputs=[pred])
        self.config = config


class BidLSTM_CRF_FEATURES(BaseModel):
    """
    A Keras implementation of BidLSTM-CRF for sequence labelling using tokens combined with 
    additional generic discrete features information.
    """

    name = 'BidLSTM_CRF_FEATURES'

    def __init__(self, config, ntags=None):

        # build input, directly feed with word embedding by the data generator
        word_input = Input(shape=(None, config.word_embedding_size), name='word_input')

        # build character based embedding
        char_input = Input(shape=(None, config.max_char_length), dtype='int32', name='char_input')
        char_embeddings = TimeDistributed(Embedding(input_dim=config.char_vocab_size,
                                    output_dim=config.char_embedding_size,
                                    mask_zero=True,
                                    #embeddings_initializer=RandomUniform(minval=-0.5, maxval=0.5),
                                    name='char_embeddings'
                                    ))(char_input)

        chars = TimeDistributed(Bidirectional(LSTM(config.num_char_lstm_units,
                                                   return_sequences=False)))(char_embeddings)

        # layout features input and embeddings
        features_input = Input(shape=(None, len(config.features_indices)), dtype='float32', name='features_input')

        # The input dimension is calculated by
        # features_vocabulary_size (default 12) * number_of_features + 1 (the zero is reserved for masking / padding)
        features_embedding = TimeDistributed(Embedding(input_dim=config.features_vocabulary_size * len(config.features_indices) + 1,
                                       output_dim=config.features_embedding_size,
                                       # mask_zero=True,
                                       trainable=False,
                                       name='features_embedding'), name="features_embedding_td")(features_input)

        features_embedding_bd = TimeDistributed(Bidirectional(LSTM(config.features_lstm_units, return_sequences=False)),
                                                 name="features_embedding_td_2")(features_embedding)

        features_embedding_out = Dropout(config.dropout)(features_embedding_bd)

        # length of sequence not used by the model, but used by the training scorer
        length_input = Input(batch_shape=(None, 1), dtype='int32', name='length_input')

        # combine characters and word embeddings
        x = Concatenate()([word_input, chars, features_embedding_out])
        x = Dropout(config.dropout)(x)

        x = Bidirectional(LSTM(units=config.num_word_lstm_units,
                               return_sequences=True,
                               recurrent_dropout=config.recurrent_dropout))(x)
        x = Dropout(config.dropout)(x)
        x = Dense(config.num_word_lstm_units, activation='tanh')(x)
        x = Dense(ntags)(x)
        self.crf = ChainCRF()
        pred = self.crf(x)

        self.model = Model(inputs=[word_input, char_input, features_input, length_input], outputs=[pred])
        self.config = config


class BERT(BaseModel):
    """
    A Keras implementation of BERT for sequence labelling with softmax activation layer. The BERT layer will be 
    loaded with weights of existing pre-trained BERT model given by the field transformer in config. 
    """

    name = 'BERT'

    def __init__(self, config, ntags=None):
        # build input, directly feed with BERT input ids by the data generator
        max_seq_len = config.max_sequence_length
        transformer_model_name = config.transformer

        transformer_model = TFBertModel.from_pretrained(transformer_model_name, from_pt=True)

        input_ids_in = Input(shape=(max_seq_len,), name='input_token', dtype='int32')
        token_type_ids = Input(shape=(max_seq_len,), name='input_token_type', dtype='int32')
        #attention_mask = Input(shape=(max_len,), dtype=tf.int32)

        embedding_layer = transformer_model(input_ids_in, token_type_ids=token_type_ids)[0]
        #embedding = transformer_model(input_ids_in, token_type_ids=token_type_ids, attention_mask=attention_mask)[0]
        embedding_layer = Dropout(0.1)(embedding_layer)
        tag_logits = Dense(ntags, activation='softmax')(embedding_layer)
        
        self.model = Model(inputs=[input_ids_in, token_type_ids], outputs=[tag_logits])
        self.config = config

    def get_generator(self):
        return DataGeneratorTransformers   

class BERT_CRF(BaseModel):
    """
    A Keras implementation of BERT-CRF for sequence labelling. The BERT layer will be loaded with weights
    of existing pre-trained BERT model given by the field transformer in the config. 
    """

    name = 'BERT_CRF'

    def __init__(self, config, ntags=None):
        # build input, directly feed with BERT input ids by the data generator AND features from data generator too
        max_seq_len = config.max_sequence_length
        transformer_model_name = config.transformer

        transformer_model = TFBertModel.from_pretrained(transformer_model_name, from_pt=True)

        input_ids_in = Input(shape=(max_seq_len,), name='input_token', dtype='int32')
        token_type_ids = Input(shape=(max_seq_len,), name='input_token_type', dtype='int32')
        #attention_mask = Input(shape=(max_len,), dtype=tf.int32)

        embedding_layer = transformer_model(input_ids_in, token_type_ids=token_type_ids)[0]
        #embedding = transformer_model(input_ids_in, token_type_ids=token_type_ids, attention_mask=attention_mask)[0]
        x = Dropout(0.1)(embedding_layer)
        x = Dense(ntags)(x)
        self.crf = ChainCRF()
        pred = self.crf(x)
        
        self.model = Model(inputs=[input_ids_in, token_type_ids], outputs=[pred])
        self.config = config

    def get_generator(self):
        return DataGeneratorTransformers


class BERT_CRF_FEATURES(BaseModel):
    """
    A Keras implementation of BERT-CRF for sequence labelling using tokens combined with 
    additional generic discrete features information. The BERT layer will be loaded with weights
    of existing pre-trained BERT model given by the field transformer in the config. 
    """

    name = 'BERT_CRF_FEATURES'

    def __init__(self, config, ntags=None):
        # build input, directly feed with BERT input ids by the data generator
        max_seq_len = config.max_sequence_length
        transformer_model_name = config.transformer

        transformer_model = TFBertModel.from_pretrained(transformer_model_name, from_pt=True)

        input_ids_in = Input(shape=(max_seq_len,), name='input_token', dtype='int32')
        token_type_ids = Input(shape=(max_seq_len,), name='input_token_type', dtype='int32')
        #attention_mask = Input(shape=(max_len,), dtype=tf.int32)

        text_embedding_layer = transformer_model(input_ids_in, oken_type_ids=token_type_ids)[0]
        #embedding = transformer_model(input_ids_in, token_type_ids=token_type_ids, attention_mask=attention_mask)[0]
        text_embedding_layer = Dropout(0.1)(text_embedding_layer)

        # layout features input and embeddings
        features_input = Input(shape=(None, len(config.features_indices)), dtype='float32', name='features_input')

        # The input dimension is calculated by
        # features_vocabulary_size (default 12) * number_of_features + 1 (the zero is reserved for masking / padding)
        features_embedding = TimeDistributed(Embedding(input_dim=config.features_vocabulary_size * len(config.features_indices) + 1,
                                       output_dim=config.features_embedding_size,
                                       # mask_zero=True,
                                       trainable=False,
                                       name='features_embedding'), name="features_embedding_td")(features_input)

        features_embedding_bd = TimeDistributed(Bidirectional(LSTM(config.features_lstm_units, return_sequences=False)),
                                                 name="features_embedding_td_2")(features_embedding)

        features_embedding_out = Dropout(config.dropout)(features_embedding_bd)

        # combine feature and text embeddings
        x = Concatenate()([text_embedding_layer, features_embedding_out])
        x = Dropout(config.dropout)(x)

        x = Bidirectional(LSTM(units=config.num_word_lstm_units,
                               return_sequences=True,
                               recurrent_dropout=config.recurrent_dropout))(x)
        x = Dropout(config.dropout)(x)
        x = Dense(config.num_word_lstm_units, activation='tanh')(x)
        x = Dense(ntags)(x)
        self.crf = ChainCRF()
        pred = self.crf(x)

        self.model = Model(inputs=[input_ids_in, features_input, token_type_ids], outputs=[pred])
        self.config = config

    def get_generator(self):
        return DataGeneratorTransformers
