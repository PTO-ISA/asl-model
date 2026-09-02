cmake_minimum_required(VERSION 3.20)
project(PTOASLModel VERSION 0.1.0 LANGUAGES C CXX)

include(CMakePackageConfigHelpers)
include(GNUInstallDirs)

option(PTO_ASL_MODEL_BUILD_TESTS "Build PTO ASL model tests" ON)

add_library(pto_asl_model STATIC src/client.cpp)
add_library(PTOASLModel::pto_asl_model ALIAS pto_asl_model)
target_compile_features(pto_asl_model PUBLIC cxx_std_20)
target_compile_options(pto_asl_model PRIVATE -Wall -Wextra -Werror)
target_include_directories(pto_asl_model
    PUBLIC
        $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
        $<INSTALL_INTERFACE:${CMAKE_INSTALL_INCLUDEDIR}>
)

add_executable(pto-model-run tools/pto-model-run.cpp)
target_link_libraries(pto-model-run PRIVATE pto_asl_model)
target_compile_options(pto-model-run PRIVATE -Wall -Wextra -Werror)

if(PTO_ASL_MODEL_BUILD_TESTS)
    enable_testing()
    add_executable(pto_asl_model_abi_c_test tests/abi_c.c)
    target_link_libraries(pto_asl_model_abi_c_test PRIVATE pto_asl_model)
    add_executable(pto_asl_model_abi_cpp_test tests/abi_cpp.cpp)
    target_link_libraries(pto_asl_model_abi_cpp_test PRIVATE pto_asl_model)
    add_executable(pto_asl_model_client_test tests/client_test.cpp)
    target_link_libraries(pto_asl_model_client_test PRIVATE pto_asl_model)
    foreach(target
            pto_asl_model_abi_c_test
            pto_asl_model_abi_cpp_test
            pto_asl_model_client_test)
        target_compile_options(${target} PRIVATE -Wall -Wextra -Werror)
    endforeach()
    add_test(NAME pto_asl_model_abi_c COMMAND pto_asl_model_abi_c_test)
    add_test(NAME pto_asl_model_abi_cpp COMMAND pto_asl_model_abi_cpp_test)
    add_test(NAME pto_asl_model_client COMMAND pto_asl_model_client_test)
endif()

install(TARGETS pto_asl_model EXPORT PTOASLModelTargets
    ARCHIVE DESTINATION ${CMAKE_INSTALL_LIBDIR})
install(TARGETS pto-model-run RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR})
install(DIRECTORY include/ DESTINATION ${CMAKE_INSTALL_INCLUDEDIR})
install(EXPORT PTOASLModelTargets
    FILE PTOASLModelTargets.cmake
    NAMESPACE PTOASLModel::
    DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/PTOASLModel)

configure_package_config_file(
    cmake/PTOASLModelConfig.cmake.in
    ${CMAKE_CURRENT_BINARY_DIR}/PTOASLModelConfig.cmake
    INSTALL_DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/PTOASLModel)
write_basic_package_version_file(
    ${CMAKE_CURRENT_BINARY_DIR}/PTOASLModelConfigVersion.cmake
    VERSION ${PROJECT_VERSION}
    COMPATIBILITY SameMajorVersion)
install(FILES
    ${CMAKE_CURRENT_BINARY_DIR}/PTOASLModelConfig.cmake
    ${CMAKE_CURRENT_BINARY_DIR}/PTOASLModelConfigVersion.cmake
    DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/PTOASLModel)
