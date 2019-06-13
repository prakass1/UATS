import keras

from generator.AugmentationGenerator import *


class ValAugmentDataGenerator(keras.utils.Sequence):

    def __init__(self, img_path, gt_path, list_IDs, batch_size=2, dim=(32, 168, 168), n_channels=1,
                 n_classes=10, shuffle=True, rotation=True):
        'Initialization'
        self.dim = dim
        self.img_path = img_path
        self.gt_path = gt_path
        self.batch_size = batch_size
        self.list_IDs = list_IDs
        self.n_channels = n_channels
        self.n_classes = n_classes
        self.shuffle = shuffle
        self.rotation = rotation
        self.on_epoch_end()

    def on_epoch_end(self):
        self.indexes = np.arange(len(self.list_IDs))

    def __data_generation(self, list_IDs_temp):
        'Generates data containing batch_size samples'  # X : (n_samples, *dim, n_channels)
        # Initialization
        X = np.empty((self.batch_size, *self.dim, self.n_channels))
        y1 = np.empty((self.batch_size, *self.dim), dtype=np.uint8)
        y2 = np.empty((self.batch_size, *self.dim), dtype=np.uint8)
        y3 = np.empty((self.batch_size, *self.dim), dtype=np.uint8)
        y4 = np.empty((self.batch_size, *self.dim), dtype=np.uint8)
        y5 = np.empty((self.batch_size, *self.dim), dtype=np.uint8)
        masks = np.empty((self.batch_size, *self.dim, 5), dtype=np.uint8)

        # Generate data
        for i, ID in enumerate(list_IDs_temp):
            aug_type = np.random.randint(0, 4)
            # print('random no ', aug_type)
            X[i, :, :, :, :], aug_gt, mask = get_single_image_augmentation_with_mask(
                aug_type,
                np.load(self.img_path + ID + '.npy'),
                np.load(self.gt_path + ID + '.npy'), img_no=ID)

            y1[i] = aug_gt[:, :, :, 0]
            y2[i] = aug_gt[:, :, :, 1]
            y3[i] = aug_gt[:, :, :, 2]
            y4[i] = aug_gt[:, :, :, 3]
            y5[i] = aug_gt[:, :, :, 4]
            masks[i] = mask

        return X, masks, y1, y2, y3, y4, y5

    def __len__(self):
        'Denotes the number of batches per epoch'
        return int(np.floor(len(self.list_IDs) / self.batch_size))

    def __getitem__(self, index):
        'Generate one batch of data'
        # Generate indexes of the batch
        indexes = self.indexes[index * self.batch_size:(index + 1) * self.batch_size]
        # print('\n')
        # print(indexes)
        # print('\n')

        # Find list of IDs
        list_IDs_temp = [self.list_IDs[k] for k in indexes]

        # Generate data
        X, masks, y1, y2, y3, y4, y5 = self.__data_generation(list_IDs_temp)

        return [X, masks], [y1, y2, y3, y4, y5]
