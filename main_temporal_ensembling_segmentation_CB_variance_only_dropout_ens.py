import tensorflow as tf
from keras import backend as K
from keras.backend.tensorflow_backend import set_session
from keras.callbacks import Callback
from keras.callbacks import ModelCheckpoint, ReduceLROnPlateau, EarlyStopping, TensorBoard, CSVLogger

from generator.data_gen import DataGenerator
from lib.segmentation.model_WN_MCdropout import build_model
from lib.segmentation.ops import ramp_up_weight, ramp_down_weight
from lib.segmentation.utils import make_train_test_dataset
from zonal_utils.AugmentationGenerator import *

TB_LOG_DIR = './tb/variance_mcdropout/7'


def train(train_x, train_y, val_x, val_y, gpu_id, nb_gpus):
    # 38 Training 20 unsupervised data.
    # hyper-params
    num_train_data = train_x.shape[0]
    num_labeled_train = 38
    num_unlabeled_train = num_train_data - num_labeled_train
    ramp_up_period = 100
    ramp_down_period = 100
    num_class = 5
    num_epoch = 351
    batch_size = 2
    weight_max = 40
    learning_rate = 5e-5
    alpha = 0.6
    EarlyStop = False
    LRScheduling = False

    # datagen list
    train_id_list = [str(i) for i in np.arange(0, train_x.shape[0])]
    val_id_list = [str(i) for i in np.arange(0, val_x.shape[0])]

    # prepare weights and arrays for updates
    gen_weight = ramp_up_weight(ramp_up_period, weight_max * (num_labeled_train / num_train_data))
    gen_lr_weight = ramp_down_weight(ramp_down_period)

    # prepare dataset
    print('-' * 30)
    print('Loading train data...')
    print('-' * 30)

    # ret_dic = split_supervised_train(train_x, train_y, num_labeled_train)

    ret_dic = make_train_test_dataset(train_x, train_y, val_x, val_y, num_labeled_train, num_class,
                                      unsupervised_target_init=True)

    unsupervised_target = ret_dic['unsupervised_target']
    supervised_label = ret_dic['supervised_label']
    supervised_flag = ret_dic['train_sup_flag']
    unsupervised_weight = ret_dic['unsupervised_weight']

    print("Images Size:", num_train_data)
    print("GT Size:", train_y.shape)

    print('-' * 30)
    print('Creating and compiling model...')
    print('-' * 30)

    # Build Model
    model = build_model(num_class=num_class, learning_rate=learning_rate, gpu_id=gpu_id, nb_gpus=nb_gpus)

    # model.metrics_tensors += model.outputs
    model.summary()

    class TemporalCallback(Callback):

        def __init__(self, img, train_idx_list, unsupervised_target, supervised_label, supervised_flag,
                     unsupervised_weight):
            self.img = img
            self.train_idx_list = train_idx_list  # list of indexes of training eg
            self.unsupervised_target = train_y
            self.supervised_label = supervised_label
            self.supervised_flag = supervised_flag
            self.unsupervised_weight = unsupervised_weight
            self.last_batch_no = num_train_data / batch_size - 1

            # initial epoch
            self.ensemble_prediction = train_y
            # self.cur_pred = np.zeros((num_train_data, 32, 168, 168, num_class))

        def on_batch_begin(self, batch, logs=None):
            pass

        def on_batch_end(self, batch, logs=None):
            # print('batch no', batch)

            if batch == self.last_batch_no and self.epoch > 5:
                inp = [self.img, self.unsupervised_target, self.supervised_label, self.supervised_flag,
                       self.unsupervised_weight]
                cur_pred = np.empty((num_train_data, 32, 168, 168, num_class))

                model_out = model.predict(inp, batch_size=2)
                model_out += model.predict(inp, batch_size=2)
                model_out += model.predict(inp, batch_size=2)

                cur_pred[:, :, :, :, 0] = model_out[0] / 3
                cur_pred[:, :, :, :, 1] = model_out[1] / 3
                cur_pred[:, :, :, :, 2] = model_out[2] / 3
                cur_pred[:, :, :, :, 3] = model_out[3] / 3
                cur_pred[:, :, :, :, 4] = model_out[4] / 3

                max = np.reshape(np.max(cur_pred, axis=-1), (num_train_data, 32, 168, 168, 1))
                cur_pred_final = np.where(cur_pred == max, max, cur_pred)
                del cur_pred

                # update ensemble_prediction and unsupervised weight when an epoch ends
                self.unsupervised_weight = 1. - np.abs(cur_pred_final - self.ensemble_prediction)

                # Z = αZ + (1 - α)z
                self.ensemble_prediction = alpha * self.ensemble_prediction + (1 - alpha) * cur_pred_final
                self.unsupervised_target = self.ensemble_prediction / (1 - alpha ** (self.epoch + 1))

        def on_epoch_begin(self, epoch, logs=None):
            self.epoch = epoch
            # print('train eg on epoch begin-',self.train_idx_list[0:num_labeled_train], 'unlabeled-',self.train_idx_list[num_labeled_train: num_train_data])

            if epoch > num_epoch - ramp_down_period:
                weight_down = next(gen_lr_weight)
                K.set_value(model.optimizer.lr, weight_down * learning_rate)
                K.set_value(model.optimizer.beta_1, 0.4 * weight_down + 0.5)
                print('LR: alpha-', K.eval(model.optimizer.lr), K.eval(model.optimizer.beta_1))

        def on_epoch_end(self, epoch, logs={}):
            # shuffle examples
            np.random.shuffle(self.train_idx_list)
            np.random.shuffle(val_id_list)
            DataGenerator.__init__(self, train_x,
                                   self.unsupervised_target,
                                   self.supervised_label,
                                   self.supervised_flag,
                                   self.unsupervised_weight,
                                   self.train_idx_list)

        def get_training_list(self):
            return self.train_idx_list

    # callbacks
    print('-' * 30)
    print('Creating callbacks...')
    print('-' * 30)
    csv_logger = CSVLogger('validation.csv', append=True, separator=';')
    model_checkpoint = ModelCheckpoint('./temporal_variance_mcdropout.h5', monitor='val_loss', save_best_only=True,
                                       verbose=1,
                                       mode='min')
    tensorboard = TensorBoard(log_dir=TB_LOG_DIR, write_graph=False, write_grads=True, histogram_freq=0,
                              batch_size=5,
                              write_images=False)
    earlyStopImprovement = EarlyStopping(monitor='val_loss', min_delta=0.001, patience=200, verbose=1, mode='min')
    LRDecay = ReduceLROnPlateau(monitor='val_loss', factor=0.8, patience=50, verbose=1, mode='min', min_lr=1e-8,
                                epsilon=0.01)

    tcb = TemporalCallback(train_x, train_id_list, unsupervised_target, supervised_label, supervised_flag,
                           unsupervised_weight)
    cb = [model_checkpoint, tcb]
    if EarlyStop:
        cb.append(earlyStopImprovement)
    if LRScheduling:
        cb.append(LRDecay)
    cb.append(tensorboard)

    print('BATCH Size = ', batch_size)

    print('Callbacks: ', cb)
    params = {'dim': (32, 168, 168),
              'batch_size': batch_size,
              'n_classes': 5,
              'n_channels': 1,
              'shuffle': True}

    print('-' * 30)
    print('Fitting model...')
    print('-' * 30)
    training_generator = DataGenerator(train_x, unsupervised_target, supervised_label, supervised_flag,
                                       unsupervised_weight, tcb.get_training_list(), **params)

    steps = num_train_data / 2

    val_unsupervised_target = val_y
    val_supervised_flag = np.ones((val_x.shape[0], 32, 168, 168, 1))
    val_unsupervised_weight = np.ones((val_x.shape[0], 32, 168, 168, 5))

    pz = val_y[:, :, :, :, 0]
    cz = val_y[:, :, :, :, 1]
    us = val_y[:, :, :, :, 2]
    afs = val_y[:, :, :, :, 3]
    bg = val_y[:, :, :, :, 4]

    y_val = [pz, cz, us, afs, bg]
    x_val = [val_x, val_unsupervised_target, val_y, val_supervised_flag, val_unsupervised_weight]

    history = model.fit_generator(generator=training_generator,
                                  steps_per_epoch=steps,
                                  validation_data=[x_val, y_val],
                                  use_multiprocessing=True,
                                  epochs=num_epoch,
                                  callbacks=cb
                                  )
    # workers=4)
    # model.save('temporal_max_ramp_final.h5')


def predict(val_x, val_y):
    nrChanels = 1

    name = 'augmented_x20_sfs16_dataGeneration_LR_'
    val_unsupervised_target = np.zeros((val_x.shape[0], 32, 168, 168, 5))
    val_supervised_flag = np.ones((val_x.shape[0], 32, 168, 168, 1))
    val_unsupervised_weight = np.zeros((val_x.shape[0], 32, 168, 168, 5))

    x_val = [val_x, val_unsupervised_target, val_y, val_supervised_flag, val_unsupervised_weight]
    y_val = [val_y[:, :, :, :, 0], val_y[:, :, :, :, 1], val_y[:, :, :, :, 2], val_y[:, :, :, :, 3],
             val_y[:, :, :, :, 4]]

    print(name)
    model = build_model(num_class=5)
    print('load_weights')
    model.load_weights('temporal_final3.h5')
    print('predict')
    out = model.predict(x_val, batch_size=1, verbose=1)

    print(model.evaluate(x_val, y_val, batch_size=1, verbose=1))
    print(name)

    np.save(name + '.npy', out)


if __name__ == '__main__':
    gpu = '/CPU:0'
    batch_size = 2
    gpu_id = '2'
    # gpu = "GPU:0"  # gpu_id (default id is first of listed in parameters)
    os.environ["CUDA_VISIBLE_DEVICES"] = gpu_id
    os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
    config = tf.ConfigProto()
    config.gpu_options.allow_growth = True
    config.allow_soft_placement = True
    set_session(tf.Session(config=config))

    nb_gpus = len(gpu_id.split(','))
    assert np.mod(batch_size, nb_gpus) == 0, \
        'batch_size should be a multiple of the nr. of gpus. ' + \
        'Got batch_size %d, %d gpus' % (batch_size, nb_gpus)
    # os.environ["CUDA_VISIBLE_DEVICES"] = '2'
    # train
    train_x = np.load('/home/suhita/zonals/data/training/trainArray_imgs_fold1.npy')
    train_y = np.load('/home/suhita/zonals/data/training/trainArray_GT_fold1.npy')
    val_x = np.load('/home/suhita/zonals/data/validation/valArray_imgs_fold1.npy')
    val_y = np.load('/home/suhita/zonals/data/validation/valArray_GT_fold1.npy')
    train(train_x, train_y, val_x, val_y, gpu, nb_gpus)

    # predict(val_x, val_y)
