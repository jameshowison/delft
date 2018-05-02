import os
import json
from utilities.Embeddings import make_embeddings_simple
import sequenceLabelling
from sequenceLabelling.tokenizer import tokenizeAndFilter
from sequenceLabelling.reader import load_data_and_labels_xml_file, load_data_and_labels_conll
import argparse
import keras.backend as K
import time

def train(embedding_vector, fold_count): 
    root = os.path.join(os.path.dirname(__file__), 'data/sequenceLabelling/toxic/')

    train_path = os.path.join(root, 'corrected.xml')
    valid_path = os.path.join(root, 'valid.xml')

    print('Loading data...')
    x_train, y_train = load_data_and_labels_xml_file(train_path)
    x_valid, y_valid = load_data_and_labels_xml_file(valid_path)
    print(len(x_train), 'train sequences')
    print(len(x_valid), 'validation sequences')

    model = sequenceLabelling.Sequence('insult', max_epoch=50, embeddings=embedding_vector)
    model.train(x_train, y_train, x_valid, y_valid)
    print('training done')

    # saving the model
    model.save()

# annotate a list of texts, provides results in a list of offset mentions 
def annotate(texts, embedding_vector, output_format):
    annotations = []

    # load model
    model = sequenceLabelling.Sequence('insult', embeddings=embedding_vector)
    model.load()
    
    start_time = time.time()

    '''
    for text in texts:
        tokens = tokenizeAndFilter(text)
        result = model.analyze(tokens)
        print(json.dumps(result, indent=4, sort_keys=True))
        if result["entities"] is not None:
            entities = result["entities"]
            annotations.append(entities)
    '''

    annotations = model.analyze(texts, output_format)
    runtime = round(time.time() - start_time, 3)

    if output_format is 'json':
        annotations["runtime"] = runtime
    else:
        print("runtime: %s seconds " % (runtime))
    return annotations


if __name__ == "__main__":

    parser = argparse.ArgumentParser(
        description = "Experimental insult recognizer for the Wikipedia toxic comments dataset")

    parser.add_argument("action")
    parser.add_argument("--fold-count", type=int, default=1)

    args = parser.parse_args()
    
    action = args.action    
    if (action != 'train') and (action != 'tag'):
        print('action not specifed, must be one of [train,tag]')

    embed_size, embedding_vector = make_embeddings_simple("/mnt/data/wikipedia/embeddings/crawl-300d-2M.vec", True)

    if action == 'train':
        if args.fold_count < 1:
            raise ValueError("fold-count should be equal or more than 1")
        train(embedding_vector, args.fold_count)

    if action == 'tag':
        someTexts = ['This is a gentle test.', 
                     'you\'re a moronic wimp who is too lazy to do research! die in hell !!', 
                     'This is a fucking test.']
        result = annotate(someTexts, embedding_vector, "json")
        print(json.dumps(result, sort_keys=False, indent=4))

    # see https://github.com/tensorflow/tensorflow/issues/3388
    K.clear_session()
