from mung.feature import FeatureType, register_feature_type
import dill as pickle
import numpy as np
import time
#from ltprg.game.color.properties.alexnet import PartialAlexnet, rgb_to_alexnet_input
from ltprg.game.color.properties.colorspace_conversions import hsls_to_rgbs, rgbs_to_labs

"""
class FeatureVisualEmbeddingType(FeatureType):
    # AlexNet layer fc-6 activations
    def __init__(self, name, paths):
        FeatureType.__init__(self)
        # paths is a list of lists of HSLs, e.g. [[H1, S1, L1], [H2, S2, L2]]
        self._name = name
        self._paths = paths # e.g. [["state.sH_0", "state.sS_0", "state.sL_0"]]
        self.cnn = PartialAlexnet('fc6')

    def get_name(self):
        return self._name

    def compute(self, datum, vec, start_index):
        output = []
        for color in self._paths:
            hsl = [datum.get(dim) for dim in color]
            rgb = np.array(hsls_to_rgbs([map(int, hsl)]))[0]
            output.extend(self.cnn.forward(rgb_to_alexnet_input(rgb)).data.numpy()[0])
        vec[start_index : len(vec)] = output[start_index : len(output)]

    def get_size(self):
        return len(self.cnn.forward(rgb_to_alexnet_input([0, 0, 0])).data.numpy()[0]) * len(self._paths) # e.g. 4096 * 3

    def get_token(self, index):
        return None

    def __eq__(self, feature_type):
        if not isinstance(feature_type, FeatureVisualEmbeddingType):
            return False
        if self._name != feature_type.name:
            return False
        return True

    def init_start(self):
        #self.cnn = PartialAlexnet('fc6')
        pass

    def init_datum(self, datum):
        pass

    def init_end(self):
        pass

    def save(self, file_path):
        obj = dict()
        obj["type"] = "FeatureVisualEmbeddingType"
        obj["name"] = self._name
        obj["paths"] = self._paths
        with open(file_path, 'w') as fp:
            pickle.dump(obj, fp)

    @staticmethod
    def load(file_path):
        with open(file_path, 'r') as fp:
            obj = pickle.load(fp)
            return FeatureVisualEmbeddingType.from_dict(obj)

    @staticmethod
    def from_dict(obj):
        name = obj["name"]
        paths = obj["paths"]
        return FeatureVisualEmbeddingType(name, paths)

register_feature_type(FeatureVisualEmbeddingType)
"""

class FeatureCielabEmbeddingType(FeatureType):
    # stimulus embeddings in CIELAB color space (3 coordinates)
    def __init__(self, name, paths, include_positions=False, position_count=1, row_count=1, standardize=False):
        FeatureType.__init__(self)
        # paths is a list of lists of HSLs, e.g. [[H1, S1, L1], [H2, S2, L2]]
        self._name = name
        self._paths = paths # e.g. [["state.sH_0", "state.sS_0", "state.sL_0"]]
        self._include_positions = include_positions
        self._position_count = position_count
        self._row_count = row_count
        self._standardize = standardize

    def get_name(self):
        return self._name

    def compute(self, datum, vec, start_index):
        output = []
        for i, color in enumerate(self._paths):
            hsl = [datum.get(dim) for dim in color]
            rgb = np.array(hsls_to_rgbs([map(int, hsl)]))[0]
            lab = np.array(rgbs_to_labs([rgb]))[0]
            
            if self._standardize:  # Range from -1 to 1
                lab[0] = ((lab[0] * 2.0) - 100.0)/100.0
                lab[1] = lab[1] / 128.0
                lab[2] = lab[2] / 128.0

            output.extend(lab)

            if self._include_positions:
                if self._row_count == 1:
                    pos = np.zeros(shape=self._position_count)
                    pos[i % self._position_count] = 1.0
                    output.extend(pos)
                else:
                    pos_index = i % self._position_count
                    col_count = self._position_count / self._row_count
                    col_index = pos_index % col_count
                    row_index = int(pos_index / col_count)
                    row_pos = np.zeros(shape=self._row_count)
                    col_pos = np.zeros(shape=col_count)
                    row_pos[row_index] = 1.0
                    col_pos[col_index] = 1.0
                    output.extend(row_pos)
                    output.extend(col_pos)

        vec[start_index : len(vec)] = output[start_index : len(output)]

    def get_size(self):
        if self._include_positions:
            if self._row_count == 1:
                return (3 + self._position_count) * len(self._paths)
            else:
                return (3 + self._row_count + self._position_count/self._row_count) * len(self._paths)
        else:
            return 3 * len(self._paths)

    def get_token(self, index):
        return None

    def __eq__(self, feature_type):
        if not isinstance(feature_type, FeatureCielabEmbeddingType):
            return False
        if self._name != feature_type.name:
            return False
        return True

    def init_start(self):
        pass

    def init_datum(self, datum):
        pass

    def init_end(self):
        pass

    def save(self, file_path):
        obj = dict()
        obj["type"] = "FeatureCielabEmbeddingType"
        obj["name"] = self._name
        obj["paths"] = self._paths
        obj["include_positions"] = self._include_positions
        obj["position_count"] = self._position_count
        obj["row_count"] = self._row_count
        obj["standardize"] = self._standardize 

        with open(file_path, 'w') as fp:
            pickle.dump(obj, fp)

    @staticmethod
    def load(file_path):
        with open(file_path, 'r') as fp:
            obj = pickle.load(fp)
            return FeatureCielabEmbeddingType.from_dict(obj)

    @staticmethod
    def from_dict(obj):
        name = obj["name"]
        paths = obj["paths"]

        include_positions = False
        position_count = 1
        row_count = 1
        standardize = False

        if "include_positions" in obj:
            include_positions = obj["include_positions"]
            position_count = obj["position_count"]
            if "row_count" in obj:
                row_count = obj["row_count"]

        if "standardize" in obj:
            standardize = obj["standardize"]

        return FeatureCielabEmbeddingType(name, paths, include_positions=include_positions, \
            position_count=position_count, row_count=row_count, standardize=standardize)

register_feature_type(FeatureCielabEmbeddingType)
